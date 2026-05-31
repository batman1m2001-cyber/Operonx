//! `KeycloakTokenProvider` — background-refreshed bearer token provider.
//!
//! Mirrors Python [`operonx/providers/auth/keycloak.py`](../../../../../operonx/providers/auth/keycloak.py).
//! Per plan §5b.7 the Python side uses BOTH a background refresh thread AND
//! a lazy fallback fetch. Rust mirrors this with:
//! - `tokio::task` background refresher (abort-on-drop, graceful shutdown).
//! - `OnceLock<RwLock<CachedToken>>` for the cached token.
//! - `get_token()` lazy-fetches on first call; subsequent calls return the
//!   cached value (or force a fetch when the current token is expired).
//!
//! # Phase 5 scope
//! Provider struct + cached token state + `get_token()` surface. The HTTP
//! fetch body lands in Phase 5b — until then `fetch_token()` returns
//! [`OperonError::Provider`].

use std::sync::Arc;
use std::time::{Duration, Instant};

use parking_lot::RwLock;
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;
use tracing::{debug, warn};

use super::config::KeycloakTokenConfig;
use crate::core::exceptions::OperonError;

#[derive(Debug, Clone)]
struct CachedToken {
    token: String,
    expires_at: Option<Instant>,
}

/// Keycloak bearer-token provider.
pub struct KeycloakTokenProvider {
    pub config: KeycloakTokenConfig,
    cache: Arc<RwLock<Option<CachedToken>>>,
    cancel: CancellationToken,
    refresh_handle: parking_lot::Mutex<Option<JoinHandle<()>>>,
}

impl std::fmt::Debug for KeycloakTokenProvider {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("KeycloakTokenProvider")
            .field("name", &self.config.name)
            .field("url", &self.config.url)
            .finish()
    }
}

impl KeycloakTokenProvider {
    /// Build a new provider. Does **not** fetch the first token or start
    /// background refresh — call [`start`](Self::start) for that.
    pub fn new(config: KeycloakTokenConfig) -> Self {
        Self {
            config,
            cache: Arc::new(RwLock::new(None)),
            cancel: CancellationToken::new(),
            refresh_handle: parking_lot::Mutex::new(None),
        }
    }

    /// Start the background refresh task. Idempotent — repeat calls are
    /// no-ops.
    pub fn start(&self) {
        let mut guard = self.refresh_handle.lock();
        if guard.is_some() {
            return;
        }
        let cache = self.cache.clone();
        let cancel = self.cancel.clone();
        let interval = Duration::from_secs_f64(self.config.refresh_interval.max(30.0));
        let name = self.config.name.clone();
        let handle = tokio::spawn(async move {
            loop {
                tokio::select! {
                    _ = cancel.cancelled() => {
                        debug!("keycloak refresh loop cancelled for {}", name);
                        return;
                    }
                    _ = tokio::time::sleep(interval) => {
                        // Phase 5b: call fetch_token and update `cache`.
                        warn!("keycloak refresh pending (not yet implemented) for {}", name);
                        let _ = cache.read();
                    }
                }
            }
        });
        *guard = Some(handle);
    }

    /// Return the current access token, lazily fetching if nothing is cached.
    pub async fn get_token(&self) -> Result<String, OperonError> {
        {
            let cache = self.cache.read();
            if let Some(c) = cache.as_ref() {
                if c.expires_at.map(|e| e > Instant::now()).unwrap_or(true) {
                    return Ok(c.token.clone());
                }
            }
        }
        // Cache miss or expired — fetch once, dedupe via the write lock.
        let mut cache = self.cache.write();
        if let Some(c) = cache.as_ref() {
            if c.expires_at.map(|e| e > Instant::now()).unwrap_or(true) {
                return Ok(c.token.clone());
            }
        }
        let (token, expires_in) = self.fetch_token().await?;
        let expires_at = expires_in.map(|secs| {
            Instant::now() + Duration::from_secs_f64((secs - self.config.refresh_buffer).max(0.0))
        });
        *cache = Some(CachedToken {
            token: token.clone(),
            expires_at,
        });
        Ok(token)
    }

    /// Stop the refresh loop.
    pub fn shutdown(&self) {
        self.cancel.cancel();
        if let Some(handle) = self.refresh_handle.lock().take() {
            handle.abort();
        }
    }

    // ── Private helpers ──────────────────────────────────────────────────

    /// HTTP POST to `config.url` → parse out the token + optional expiry.
    ///
    /// Standard OIDC `client_credentials` grant. Body is
    /// `grant_type=client_credentials&client_id=<name>&client_secret=<secret>`
    /// form-encoded. Response is JSON; `token_path` (dot-separated) extracts
    /// the access token, `expires_in_path` extracts the lifetime in seconds.
    async fn fetch_token(&self) -> Result<(String, Option<f64>), OperonError> {
        use serde_json::Value;
        let form: [(&str, &str); 3] = [
            ("grant_type", "client_credentials"),
            ("client_id", &self.config.name),
            ("client_secret", &self.config.secret),
        ];
        let resp = crate::providers::http::get_client()
            .post(&self.config.url)
            .form(&form)
            .send()
            .await
            .map_err(|e| OperonError::Provider(format!("keycloak fetch: {}", e)))?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(OperonError::Provider(format!(
                "keycloak status={}: {}",
                status, text
            )));
        }
        let body: Value = resp
            .json()
            .await
            .map_err(|e| OperonError::Provider(format!("keycloak json: {}", e)))?;
        let token = walk_path(&body, &self.config.token_path).and_then(|v| match v {
            Value::String(s) => Some(s.clone()),
            other => Some(other.to_string()),
        });
        let token = token.ok_or_else(|| {
            OperonError::Provider(format!(
                "keycloak: token not found at path '{}'",
                self.config.token_path
            ))
        })?;
        let expires_in = self
            .config
            .expires_in_path
            .as_deref()
            .and_then(|p| walk_path(&body, p))
            .and_then(|v| v.as_f64());
        Ok((token, expires_in))
    }
}

/// Walk a dot-separated path into a JSON Value (e.g. `"data.access_token"`).
fn walk_path<'a>(root: &'a serde_json::Value, path: &str) -> Option<&'a serde_json::Value> {
    let mut current = root;
    for seg in path.split('.') {
        if seg.is_empty() {
            continue;
        }
        current = current.get(seg)?;
    }
    Some(current)
}

impl Drop for KeycloakTokenProvider {
    fn drop(&mut self) {
        self.shutdown();
    }
}
