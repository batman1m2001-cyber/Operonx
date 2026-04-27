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
    /// Phase 5b adds the real body; Phase 5 ships the shape.
    async fn fetch_token(&self) -> Result<(String, Option<f64>), OperonError> {
        Err(OperonError::Provider(format!(
            "KeycloakTokenProvider::fetch_token not yet implemented (Phase 5b) — url={}",
            self.config.url
        )))
    }
}

impl Drop for KeycloakTokenProvider {
    fn drop(&mut self) {
        self.shutdown();
    }
}
