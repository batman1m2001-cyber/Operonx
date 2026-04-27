//! Auth plugin registration.
//!
//! Mirrors Python [`operonx/providers/registry/auth_plugin.py`](../../../../../operonx/providers/registry/auth_plugin.py).

use std::sync::Arc;

use serde_json::Value;

use super::KeycloakResource;
use crate::core::exceptions::OperonError;
use crate::core::registry::{registry, ConfigDict, Factory, ResourceInstance};
use crate::providers::auth::{create_auth, KeycloakTokenConfig};

/// Register the keycloak category factory. Idempotent.
pub fn register() -> Result<(), OperonError> {
    if registry().get_factory("keycloak").is_some() {
        return Ok(());
    }
    let factory: Factory = Arc::new(|cfg: ConfigDict| {
        let typed: KeycloakTokenConfig = serde_json::from_value(Value::Object(cfg))?;
        let provider = create_auth(typed)?;
        Ok(Arc::new(KeycloakResource(provider)) as ResourceInstance)
    });
    registry().register("keycloak", factory, Some("KeycloakTokenConfig"))
}
