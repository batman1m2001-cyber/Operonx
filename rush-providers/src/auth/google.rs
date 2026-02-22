//! Google service account OAuth2 — for Gemini on Vertex AI.
//!
//! Implements JWT-based service account authentication:
//! 1. Create a JWT assertion signed with the private key (RS256)
//! 2. Exchange JWT for an OAuth2 access token
//! 3. Cache and auto-refresh before expiry
//!
//! Mirrors hush-providers gemini.py token management with exponential backoff.

use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use jsonwebtoken::{encode, Algorithm, EncodingKey, Header};
use serde::{Deserialize, Serialize};
use tokio::sync::Mutex;

use crate::config::llm::GeminiConfig;
use crate::http::{get_client, ProviderError, ProviderResult};

/// Google service account token provider.
pub struct GoogleTokenProvider {
    config: GoogleServiceAccountConfig,
    state: Arc<Mutex<TokenState>>,
}

/// Extracted service account fields needed for auth.
#[derive(Clone)]
pub struct GoogleServiceAccountConfig {
    pub client_email: String,
    pub private_key: String,
    pub token_uri: String,
}

impl GoogleServiceAccountConfig {
    pub fn from_gemini_config(config: &GeminiConfig) -> Self {
        GoogleServiceAccountConfig {
            client_email: config.client_email.clone(),
            private_key: config.private_key.clone(),
            token_uri: config.token_uri.clone(),
        }
    }
}

struct TokenState {
    access_token: Option<String>,
    expires_at: f64,
}

/// JWT claims for Google service account.
#[derive(Serialize)]
struct JwtClaims {
    iss: String,
    scope: String,
    aud: String,
    iat: i64,
    exp: i64,
}

/// Token response from Google OAuth2.
#[derive(Deserialize)]
struct TokenResponse {
    access_token: String,
    #[allow(dead_code)]
    token_type: Option<String>,
    expires_in: Option<i64>,
}

impl GoogleTokenProvider {
    pub fn new(config: GoogleServiceAccountConfig) -> Self {
        GoogleTokenProvider {
            config,
            state: Arc::new(Mutex::new(TokenState {
                access_token: None,
                expires_at: 0.0,
            })),
        }
    }

    /// Get a valid access token — returns cached if not expired, refreshes otherwise.
    /// Uses exponential backoff on failure (1s, 2s, 4s — 3 retries).
    pub async fn get_token(&self) -> ProviderResult<String> {
        let mut state = self.state.lock().await;

        let now = current_timestamp();
        // Refresh 5 minutes before expiry (matches Python's refresh_buffer)
        if let Some(ref token) = state.access_token {
            if now < state.expires_at - 300.0 {
                return Ok(token.clone());
            }
        }

        // Fetch with exponential backoff
        let mut last_err = None;
        for attempt in 0..3 {
            match self.fetch_access_token().await {
                Ok((token, expires_at)) => {
                    state.access_token = Some(token.clone());
                    state.expires_at = expires_at;
                    return Ok(token);
                }
                Err(e) => {
                    last_err = Some(e);
                    if attempt < 2 {
                        let delay = Duration::from_secs(1 << attempt); // 1s, 2s, 4s
                        tokio::time::sleep(delay).await;
                    }
                }
            }
        }

        Err(last_err.unwrap_or_else(|| ProviderError {
            message: "Failed to get Google access token after 3 retries".to_string(),
            status_code: None,
            error_code: None,
        }))
    }

    /// Create a JWT and exchange it for an access token.
    async fn fetch_access_token(&self) -> ProviderResult<(String, f64)> {
        let now = current_timestamp() as i64;

        // 1. Create JWT assertion
        let claims = JwtClaims {
            iss: self.config.client_email.clone(),
            scope: "https://www.googleapis.com/auth/cloud-platform".to_string(),
            aud: self.config.token_uri.clone(),
            iat: now,
            exp: now + 3600, // 1 hour
        };

        let header = Header::new(Algorithm::RS256);
        let key = EncodingKey::from_rsa_pem(self.config.private_key.as_bytes())
            .map_err(|e| ProviderError {
                message: format!("Invalid RSA private key: {}", e),
                status_code: None,
                error_code: None,
            })?;

        let jwt = encode(&header, &claims, &key).map_err(|e| ProviderError {
            message: format!("Failed to create JWT: {}", e),
            status_code: None,
            error_code: None,
        })?;

        // 2. Exchange JWT for access token
        let client = get_client();
        let resp = client
            .post(&self.config.token_uri)
            .form(&[
                (
                    "grant_type",
                    "urn:ietf:params:oauth:grant-type:jwt-bearer",
                ),
                ("assertion", &jwt),
            ])
            .send()
            .await
            .map_err(|e| ProviderError {
                message: format!("Google OAuth2 token exchange failed: {}", e),
                status_code: None,
                error_code: None,
            })?;

        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(ProviderError {
                message: format!("Google OAuth2 error (HTTP {}): {}", status.as_u16(), body),
                status_code: Some(status.as_u16()),
                error_code: None,
            });
        }

        let token_resp: TokenResponse = resp.json().await.map_err(|e| ProviderError {
            message: format!("Failed to parse Google token response: {}", e),
            status_code: Some(status.as_u16()),
            error_code: None,
        })?;

        let expires_in = token_resp.expires_in.unwrap_or(3600) as f64;
        let expires_at = current_timestamp() + expires_in;

        Ok((token_resp.access_token, expires_at))
    }
}

fn current_timestamp() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_secs_f64()
}
