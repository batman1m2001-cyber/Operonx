//! `create_auth` — build a [`KeycloakTokenProvider`] from its config.
//!
//! Mirrors Python [`operonx/providers/auth/factory.py`](../../../../../operonx/providers/auth/factory.py).
//! Per plan §5b.6 the factory is **Keycloak-only** — no multi-type
//! dispatcher. Expand only when a second auth provider is added.

use std::sync::Arc;

use super::config::KeycloakTokenConfig;
use super::keycloak::KeycloakTokenProvider;
use crate::core::exceptions::OperonError;

/// Construct a [`KeycloakTokenProvider`] and start its background refresh
/// loop.
pub fn create_auth(
    config: KeycloakTokenConfig,
) -> Result<Arc<KeycloakTokenProvider>, OperonError> {
    let provider = Arc::new(KeycloakTokenProvider::new(config));
    provider.start();
    Ok(provider)
}
