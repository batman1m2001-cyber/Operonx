//! `ConfigRegistry` — global registry mapping resource categories to factories.
//!
//! Mirrors Python [`operon/core/registry/config_registry.py`](../../../../../operon/core/registry/config_registry.py).
//!
//! # Rust-vs-Python tweaks
//! Python uses the Pydantic config *class* as the registration handle:
//!
//! ```python
//! REGISTRY.register(LLMConfig, LLMFactory.create)   # class → factory
//! config_class = REGISTRY.get_class("llm")          # category → class
//! instance     = REGISTRY.create(config_object)     # typed obj → instance
//! ```
//!
//! Rust doesn't have runtime class objects, so we collapse to a single
//! map: **`category → factory`**. Each factory accepts a [`ConfigDict`]
//! (the raw JSON/YAML object that storage returned) and is responsible for
//! its own deserialization via `serde_json::from_value::<MyConfig>(…)`.
//! This keeps the registry fully type-erased without forcing every provider
//! crate to hand us a trait object.
//!
//! Providers wire up via `inventory::submit!` in their `registry/*_plugin.rs`
//! file (Phase 6) — no runtime startup code required.

use std::any::Any;
use std::sync::{Arc, OnceLock};

use dashmap::DashMap;
use tracing::debug;

use super::storage::ConfigDict;
use crate::core::exceptions::OperonError;

/// A type-erased resource instance handed back by a factory.
///
/// Providers downcast to their concrete type via `Arc::downcast::<T>()`.
pub type ResourceInstance = Arc<dyn Any + Send + Sync>;

/// Factory signature: raw config dict → typed resource instance.
///
/// The factory owns parsing and validation (`serde_json::from_value`), so the
/// registry itself stays schema-agnostic.
pub type Factory = Arc<
    dyn Fn(ConfigDict) -> Result<ResourceInstance, OperonError> + Send + Sync + 'static,
>;

/// One registration — a factory plus the optional "schema name" the config
/// deserializes into (used only for diagnostics / `export_config`).
#[derive(Clone)]
pub struct ConfigEntry {
    /// The registered factory.
    pub factory: Factory,
    /// Optional human name for the config type (e.g. `"LLMConfig"`). Purely
    /// informational — not used for dispatch.
    pub schema_name: Option<&'static str>,
}

impl std::fmt::Debug for ConfigEntry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ConfigEntry")
            .field("schema_name", &self.schema_name)
            .field("factory", &"<fn>")
            .finish()
    }
}

/// Global registry keyed by **category**.
#[derive(Debug, Default)]
pub struct ConfigRegistry {
    entries: DashMap<String, ConfigEntry>,
}

impl ConfigRegistry {
    /// Fresh, empty registry. Use [`instance`] for the global singleton.
    pub fn new() -> Self {
        Self::default()
    }

    /// Register `factory` under `category` (e.g. `"llm"`, `"embedding"`).
    ///
    /// Duplicate registrations for the same category are an error — the second
    /// caller must `remove` first or rename.
    pub fn register(
        &self,
        category: impl Into<String>,
        factory: Factory,
        schema_name: Option<&'static str>,
    ) -> Result<(), OperonError> {
        let category = category.into();
        if self.entries.contains_key(&category) {
            return Err(OperonError::ResourceHub(format!(
                "duplicate category '{}' — factory already registered",
                category
            )));
        }
        self.entries.insert(
            category.clone(),
            ConfigEntry {
                factory,
                schema_name,
            },
        );
        debug!("registered: {} -> {:?}", category, schema_name);
        Ok(())
    }

    /// Look up the entry for `category`.
    pub fn get_entry(&self, category: &str) -> Option<ConfigEntry> {
        self.entries.get(category).map(|r| r.clone())
    }

    /// Look up just the factory for `category`.
    pub fn get_factory(&self, category: &str) -> Option<Factory> {
        self.entries.get(category).map(|r| r.factory.clone())
    }

    /// Instantiate a resource from its category + raw config dict.
    ///
    /// Mirrors Python's `REGISTRY.create(config)` — except we dispatch by
    /// `category` string rather than by Python class object.
    pub fn create(
        &self,
        category: &str,
        config: ConfigDict,
    ) -> Result<ResourceInstance, OperonError> {
        let factory = self.get_factory(category).ok_or_else(|| {
            OperonError::ResourceHub(format!(
                "no factory registered for category '{}'",
                category
            ))
        })?;
        factory(config)
    }

    /// Drop every registration. Tests only.
    pub fn clear(&self) {
        self.entries.clear();
    }

    /// All registered category names.
    pub fn categories(&self) -> Vec<String> {
        self.entries.iter().map(|r| r.key().clone()).collect()
    }
}

/// Global [`ConfigRegistry`] singleton.
///
/// Accessed via the [`registry`] free function — Python's `REGISTRY` module
/// global becomes a zero-arg function call here.
static GLOBAL: OnceLock<ConfigRegistry> = OnceLock::new();

/// Accessor for the global [`ConfigRegistry`]. Python-parity for
/// `from operon.core.registry import REGISTRY`.
pub fn registry() -> &'static ConfigRegistry {
    GLOBAL.get_or_init(ConfigRegistry::new)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[derive(Debug)]
    struct Dummy {
        pub url: String,
    }

    fn dummy_factory() -> Factory {
        Arc::new(|cfg: ConfigDict| {
            let url = cfg
                .get("url")
                .and_then(|v| v.as_str())
                .ok_or_else(|| OperonError::Config("missing url".into()))?
                .to_string();
            Ok(Arc::new(Dummy { url }) as ResourceInstance)
        })
    }

    #[test]
    fn register_and_create_roundtrip() {
        let reg = ConfigRegistry::new();
        reg.register("dummy", dummy_factory(), Some("DummyConfig"))
            .unwrap();

        let mut cfg = serde_json::Map::new();
        cfg.insert("url".into(), json!("http://example.com"));
        let inst = reg.create("dummy", cfg).unwrap();
        let dummy = inst.downcast::<Dummy>().expect("downcast to Dummy");
        assert_eq!(dummy.url, "http://example.com");
    }

    #[test]
    fn duplicate_category_errors() {
        let reg = ConfigRegistry::new();
        reg.register("dup", dummy_factory(), None).unwrap();
        let err = reg.register("dup", dummy_factory(), None).unwrap_err();
        assert!(matches!(err, OperonError::ResourceHub(_)));
    }

    #[test]
    fn unknown_category_errors() {
        let reg = ConfigRegistry::new();
        let err = reg.create("nope", serde_json::Map::new()).unwrap_err();
        assert!(matches!(err, OperonError::ResourceHub(_)));
    }
}
