//! Auth provider configuration.
//!
//! Mirrors Python [`operon/providers/auth/config.py`](../../../../../operon/providers/auth/config.py).
//! Only [`KeycloakTokenConfig`] is defined — per plan §5b.6 `create_auth()`
//! is Keycloak-only, not a multi-type dispatcher.

use serde::{Deserialize, Serialize};

fn default_token_path() -> String {
    "accessToken".into()
}
fn default_refresh_interval() -> f64 {
    3600.0
}
fn default_refresh_buffer() -> f64 {
    300.0
}

/// Configuration for a Keycloak-style token endpoint.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct KeycloakTokenConfig {
    /// Token endpoint URL.
    pub url: String,
    /// Client / application name.
    pub name: String,
    /// Client secret.
    pub secret: String,

    /// JSON-pointer-ish key path that extracts the access token from the
    /// response (e.g. `"accessToken"` or `"data.token"`).
    #[serde(default = "default_token_path")]
    pub token_path: String,

    /// Optional JSON path that extracts the expiry (seconds). `None` means
    /// fall back to `refresh_interval` for scheduling.
    #[serde(default)]
    pub expires_in_path: Option<String>,

    /// Background refresh cadence in seconds. Defaults to 1h.
    #[serde(default = "default_refresh_interval")]
    pub refresh_interval: f64,

    /// Refresh this many seconds before the recorded expiry. Defaults to 5m.
    #[serde(default = "default_refresh_buffer")]
    pub refresh_buffer: f64,
}
