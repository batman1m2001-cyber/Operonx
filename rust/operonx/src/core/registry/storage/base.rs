//! `ConfigStorage` — pluggable storage backend for resource configs.
//!
//! Mirrors Python [`operon/core/registry/storage/base.py`](../../../../../../operon/core/registry/storage/base.py).
//! YAML and JSON backends live alongside this file; user code can implement
//! this trait for MongoDB, Redis, etc.
//!
//! # Sync, not async
//! Storage reads happen at startup (or first-access lazy load) — not on the
//! per-op hot path. Keeping the trait sync avoids async-trait overhead and
//! matches the Python sync API one-for-one.

use std::collections::HashMap;

use serde_json::Value;

use crate::core::exceptions::OperonError;

/// A serialized config dict keyed by `"category:name"`.
pub type ConfigDict = serde_json::Map<String, Value>;

/// Storage backend trait for resource configs.
///
/// Each backend persists a map of `"category:name"` → JSON object. Keys
/// **must** contain a single `":"` (e.g. `"llm:gpt-4o"`, `"embedding:bge-m3"`).
pub trait ConfigStorage: Send + Sync {
    /// Load a single config by key. Returns `Ok(None)` when the key is absent.
    fn load_one(&self, key: &str) -> Result<Option<ConfigDict>, OperonError>;

    /// Load every config in the backend keyed by its registry key.
    fn load_all(&self) -> Result<HashMap<String, ConfigDict>, OperonError>;

    /// Persist a config under `key`. Returns `true` if the write succeeded.
    fn save(&self, key: &str, config: ConfigDict) -> Result<bool, OperonError>;

    /// Remove a config. Returns `true` if it existed, `false` otherwise.
    fn remove(&self, key: &str) -> Result<bool, OperonError>;

    /// Close any held resources (file handles, db connections, …).
    fn close(&self) -> Result<(), OperonError> {
        Ok(())
    }
}
