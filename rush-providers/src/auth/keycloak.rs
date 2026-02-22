//! Keycloak token provider — mirrors hush-providers auth/keycloak.py.
//!
//! Fetches and caches OAuth2 tokens from a Keycloak endpoint.
//! Background refresh via tokio task (daemon-like, doesn't block shutdown).

use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tokio::sync::Mutex;

use crate::http::{get_client, ProviderError, ProviderResult};

/// Keycloak token provider configuration.
#[derive(Debug, Clone)]
pub struct KeycloakConfig {
    /// Token endpoint URL.
    pub url: String,
    /// Client name/ID.
    pub name: String,
    /// Client secret.
    pub secret: String,
    /// JSON path to extract token from response (default: "accessToken").
    pub token_path: String,
    /// JSON path to extract expiry from response (default: "expiresIn").
    pub expires_in_path: Option<String>,
    /// Background refresh interval in seconds (default: 3600).
    pub refresh_interval: f64,
    /// Seconds before expiry to proactively refresh (default: 300).
    pub refresh_buffer: f64,
}

impl Default for KeycloakConfig {
    fn default() -> Self {
        KeycloakConfig {
            url: String::new(),
            name: String::new(),
            secret: String::new(),
            token_path: "accessToken".to_string(),
            expires_in_path: Some("expiresIn".to_string()),
            refresh_interval: 3600.0,
            refresh_buffer: 300.0,
        }
    }
}

/// Thread-safe Keycloak token provider.
/// Caches tokens and refreshes proactively before expiry.
pub struct KeycloakTokenProvider {
    config: KeycloakConfig,
    state: Arc<Mutex<TokenState>>,
}

struct TokenState {
    token: Option<String>,
    expires_at: f64, // UNIX timestamp
}

impl KeycloakTokenProvider {
    pub fn new(config: KeycloakConfig) -> Self {
        KeycloakTokenProvider {
            config,
            state: Arc::new(Mutex::new(TokenState {
                token: None,
                expires_at: 0.0,
            })),
        }
    }

    /// Get a valid token — returns cached if not expired, fetches new otherwise.
    pub async fn get_token(&self) -> ProviderResult<String> {
        let mut state = self.state.lock().await;

        let now = current_timestamp();
        if let Some(ref token) = state.token {
            if now < state.expires_at {
                return Ok(token.clone());
            }
        }

        // Token missing or expired — fetch new
        let (token, expires_at) = self.fetch_token().await?;
        state.token = Some(token.clone());
        state.expires_at = expires_at;
        Ok(token)
    }

    /// Invalidate cached token (force refresh on next call).
    pub async fn invalidate(&self) {
        let mut state = self.state.lock().await;
        state.token = None;
        state.expires_at = 0.0;
    }

    /// Fetch a new token from the Keycloak endpoint.
    async fn fetch_token(&self) -> ProviderResult<(String, f64)> {
        let client = get_client();

        let body = serde_json::json!({
            "Name": &self.config.name,
            "Secret": &self.config.secret,
        });

        let resp = client
            .post(&self.config.url)
            .header("Content-Type", "application/json")
            .json(&body)
            .send()
            .await
            .map_err(|e| ProviderError {
                message: format!("Keycloak token fetch failed: {}", e),
                status_code: None,
                error_code: None,
            })?;

        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(ProviderError {
                message: format!("Keycloak error (HTTP {}): {}", status.as_u16(), body),
                status_code: Some(status.as_u16()),
                error_code: None,
            });
        }

        let data: serde_json::Value = resp.json().await.map_err(|e| ProviderError {
            message: format!("Failed to parse Keycloak response: {}", e),
            status_code: Some(status.as_u16()),
            error_code: None,
        })?;

        // Extract token using token_path
        let token = data
            .get(&self.config.token_path)
            .and_then(|v| v.as_str())
            .ok_or_else(|| ProviderError {
                message: format!(
                    "Keycloak response missing '{}' field",
                    self.config.token_path
                ),
                status_code: None,
                error_code: None,
            })?
            .to_string();

        // Extract expiry
        let now = current_timestamp();
        let expires_at = if let Some(ref expires_path) = self.config.expires_in_path {
            let expires_in = data
                .get(expires_path)
                .and_then(|v| v.as_f64())
                .unwrap_or(self.config.refresh_interval);
            now + expires_in - self.config.refresh_buffer
        } else {
            now + self.config.refresh_interval - self.config.refresh_buffer
        };

        Ok((token, expires_at))
    }

    /// Start a background refresh task (fire-and-forget).
    ///
    /// The task refreshes the token periodically. Runs until the provider
    /// is dropped (uses weak reference pattern via Arc).
    pub fn start_background_refresh(&self, runtime: &tokio::runtime::Runtime) {
        let config = self.config.clone();
        let state = Arc::clone(&self.state);
        let weak_state = Arc::downgrade(&self.state);

        runtime.spawn(async move {
            let interval = Duration::from_secs_f64(config.refresh_interval);
            loop {
                tokio::time::sleep(interval).await;

                // Stop if the provider has been dropped
                if weak_state.upgrade().is_none() {
                    break;
                }

                // Check if token needs refresh
                let needs_refresh = {
                    let s = state.lock().await;
                    let now = current_timestamp();
                    s.token.is_none() || now >= s.expires_at
                };

                if needs_refresh {
                    match fetch_keycloak_token(&config).await {
                        Ok((token, expires_at)) => {
                            let mut s = state.lock().await;
                            s.token = Some(token);
                            s.expires_at = expires_at;
                        }
                        Err(_) => {
                            // Log error but continue — next get_token() will retry
                        }
                    }
                }
            }
        });
    }
}

/// Standalone fetch function for background refresh task.
async fn fetch_keycloak_token(config: &KeycloakConfig) -> ProviderResult<(String, f64)> {
    let client = get_client();

    let body = serde_json::json!({
        "Name": &config.name,
        "Secret": &config.secret,
    });

    let resp = client
        .post(&config.url)
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("Keycloak refresh error: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    let data: serde_json::Value = resp.json().await.map_err(|e| ProviderError {
        message: format!("Failed to parse Keycloak response: {}", e),
        status_code: Some(status.as_u16()),
        error_code: None,
    })?;

    let token = data
        .get(&config.token_path)
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string();

    let now = current_timestamp();
    let expires_at = if let Some(ref expires_path) = config.expires_in_path {
        let expires_in = data
            .get(expires_path)
            .and_then(|v| v.as_f64())
            .unwrap_or(config.refresh_interval);
        now + expires_in - config.refresh_buffer
    } else {
        now + config.refresh_interval - config.refresh_buffer
    };

    Ok((token, expires_at))
}

fn current_timestamp() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_secs_f64()
}

/// Deserialize KeycloakConfig from a serde_json::Value dict.
impl KeycloakConfig {
    pub fn from_json(val: &serde_json::Value) -> Option<Self> {
        let obj = val.as_object()?;
        Some(KeycloakConfig {
            url: obj.get("url")?.as_str()?.to_string(),
            name: obj.get("name")?.as_str()?.to_string(),
            secret: obj.get("secret")?.as_str()?.to_string(),
            token_path: obj
                .get("token_path")
                .and_then(|v| v.as_str())
                .unwrap_or("accessToken")
                .to_string(),
            expires_in_path: obj
                .get("expires_in_path")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            refresh_interval: obj
                .get("refresh_interval")
                .and_then(|v| v.as_f64())
                .unwrap_or(3600.0),
            refresh_buffer: obj
                .get("refresh_buffer")
                .and_then(|v| v.as_f64())
                .unwrap_or(300.0),
        })
    }
}
