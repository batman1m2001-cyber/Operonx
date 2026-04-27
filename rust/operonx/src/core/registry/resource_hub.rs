//! `ResourceHub` — centralized registry with lazy loading + pluggable storage.
//!
//! Mirrors Python [`operonx/core/registry/resource_hub.py`](../../../../../operonx/core/registry/resource_hub.py).
//!
//! # Lifecycle
//! 1. `ResourceHub::from_yaml(path)` / `from_json(path)` builds a hub around
//!    a storage backend.
//! 2. `hub.get("llm:gpt-4o")` — on first access, loads the raw config from
//!    storage, dispatches through [`ConfigRegistry`] by category, caches the
//!    resulting [`ResourceInstance`].
//! 3. Subsequent `hub.get(...)` calls return the cached instance.
//!
//! # Keycloak token resolution
//! Python's hub resolves `api_key: "keycloak:<name>"` references by fetching
//! a fresh token from a `KeycloakTokenProvider`. Rust ports this as a hook:
//! the resolver is a no-op until `providers/auth/keycloak.rs` lands in
//! Phase 5 — the skeleton is already in place here.
//!
//! [`ConfigRegistry`]: crate::core::registry::config_registry::ConfigRegistry
//! [`ResourceInstance`]: crate::core::registry::config_registry::ResourceInstance

use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};

use dashmap::DashMap;
use parking_lot::Mutex;
use tracing::{debug, warn};

use super::config_registry::{registry, ResourceInstance};
use super::shortcuts::HealthCheckResult;
use super::storage::{ConfigDict, ConfigStorage, JsonConfigStorage, YamlConfigStorage};
use crate::core::exceptions::OperonError;

/// Cached entry: raw config dict + optional materialized instance.
#[derive(Clone, Debug)]
pub struct CacheEntry {
    /// Raw (env-interpolated) config dict as loaded from storage.
    pub config: ConfigDict,
    /// Lazily-initialized resource instance. `None` until first `get`.
    pub instance: Option<ResourceInstance>,
}

impl CacheEntry {
    fn new(config: ConfigDict) -> Self {
        Self {
            config,
            instance: None,
        }
    }
}

/// Centralized registry for application resources.
///
/// The hub owns:
/// - a storage backend ([`ConfigStorage`])
/// - a per-key cache of loaded configs + materialized instances
///
/// Construction methods: [`from_yaml`](Self::from_yaml),
/// [`from_json`](Self::from_json). The engine installs the hub as a global
/// via [`set_instance`](Self::set_instance); tests can reset with
/// [`reset_instance`](Self::reset_instance).
pub struct ResourceHub {
    storage: Arc<dyn ConfigStorage>,
    cache: DashMap<String, CacheEntry>,
    /// Serializes cache-miss → factory → cache-fill paths so a single slow
    /// factory doesn't run twice under contention.
    load_lock: Mutex<()>,
    /// Absolute path of the file the storage was loaded from (set by
    /// `from_yaml` / `from_json`). `None` for in-memory or test-injected
    /// storage. Surfaced in `get()` error messages so the user knows where
    /// to fix things.
    source_path: Option<PathBuf>,
}

impl std::fmt::Debug for ResourceHub {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ResourceHub")
            .field("cached_keys", &self.cache.len())
            .finish()
    }
}

impl ResourceHub {
    /// Build a hub around `storage` with no source path.
    pub fn new(storage: Arc<dyn ConfigStorage>) -> Self {
        Self::new_with_source(storage, None)
    }

    fn new_with_source(storage: Arc<dyn ConfigStorage>, source_path: Option<PathBuf>) -> Self {
        Self {
            storage,
            cache: DashMap::new(),
            load_lock: Mutex::new(()),
            source_path,
        }
    }

    /// Hub backed by a YAML file at `path` (with env-var interpolation).
    pub fn from_yaml(path: impl AsRef<Path>) -> Result<Self, OperonError> {
        let abs = path.as_ref().canonicalize().unwrap_or_else(|_| path.as_ref().to_path_buf());
        let storage = YamlConfigStorage::new(abs.clone())?;
        Ok(Self::new_with_source(Arc::new(storage), Some(abs)))
    }

    /// Hub backed by a JSON file at `path`.
    pub fn from_json(path: impl AsRef<Path>) -> Result<Self, OperonError> {
        let abs = path.as_ref().canonicalize().unwrap_or_else(|_| path.as_ref().to_path_buf());
        let storage = JsonConfigStorage::new(abs.clone())?;
        Ok(Self::new_with_source(Arc::new(storage), Some(abs)))
    }

    /// Hub with no provider configs — tests and graphs that never touch a
    /// resource. Every `load_*` / `get_*` call returns `None`.
    pub fn empty() -> Self {
        Self::new(Arc::new(EmptyStorage))
    }

    /// Absolute path the hub was loaded from, or `None` for in-memory hubs.
    pub fn source_path(&self) -> Option<&Path> {
        self.source_path.as_deref()
    }

    /// Try to install a [`ResourceHub`] from `./resources.yaml` in CWD.
    ///
    /// Behavior mirrors Python's `ResourceHub.auto()`:
    /// - If a hub is already installed, return it unchanged (idempotent).
    /// - If `./resources.yaml` exists, load it, install the singleton,
    ///   and return the new hub.
    /// - If not found, emit a `tracing::warn!` and return `None`. No
    ///   singleton is installed.
    ///
    /// Never returns an error from a missing file — the warning is the
    /// early signal that setup is incomplete.
    pub fn auto() -> Option<Arc<ResourceHub>> {
        if let Ok(g) = global().read() {
            if let Some(existing) = g.clone() {
                return Some(existing);
            }
        }

        let candidate = std::env::current_dir()
            .map(|p| p.join("resources.yaml"))
            .unwrap_or_else(|_| PathBuf::from("resources.yaml"));
        if !candidate.exists() {
            warn!(
                "No resources.yaml found at {}. ResourceHub not installed; \
                 provider ops will fail at resolution. Call \
                 ResourceHub::from_yaml(<path>) with an explicit path if \
                 your file lives elsewhere.",
                candidate.display()
            );
            return None;
        }

        match ResourceHub::from_yaml(&candidate) {
            Ok(hub) => {
                let arc = Arc::new(hub);
                ResourceHub::set_instance(arc.clone());
                Some(arc)
            }
            Err(e) => {
                warn!(
                    "Failed to load resources.yaml at {}: {}",
                    candidate.display(),
                    e
                );
                None
            }
        }
    }

    // ── Global singleton ─────────────────────────────────────────────────

    /// Get the installed global hub.
    ///
    /// Returns [`OperonError::ResourceHub`] when no hub has been installed —
    /// usually because the workflow was run without an [`Operon`] engine or
    /// a `resources.yaml` file.
    ///
    /// [`Operon`]: crate::core::engine
    pub fn instance() -> Result<Arc<ResourceHub>, OperonError> {
        let guard = global().read().expect("ResourceHub global lock poisoned");
        guard.clone().ok_or_else(|| {
            OperonError::ResourceHub(
                "ResourceHub not initialized. Install one before resolving resources:\n\
                 \u{20} operonx::bootstrap();                                  // auto-discover ./resources.yaml + .env\n\
                 Or with an explicit path:\n\
                 \u{20} operonx::ResourceHub::set_instance(\n\
                 \u{20}     std::sync::Arc::new(operonx::ResourceHub::from_yaml(\"path/to/resources.yaml\")?));"
                    .to_string(),
            )
        })
    }

    /// Install `hub` as the global singleton. Called by the engine constructor.
    pub fn set_instance(hub: Arc<ResourceHub>) {
        let mut guard = global().write().expect("ResourceHub global lock poisoned");
        *guard = Some(hub);
    }

    /// Clear the global singleton — tests only.
    pub fn reset_instance() {
        let mut guard = global().write().expect("ResourceHub global lock poisoned");
        *guard = None;
    }

    // ── Config loading (lazy) ────────────────────────────────────────────

    /// Fetch the raw config dict for `key`, loading from storage on miss.
    fn load_config(&self, key: &str) -> Result<Option<ConfigDict>, OperonError> {
        if let Some(entry) = self.cache.get(key) {
            return Ok(Some(entry.config.clone()));
        }

        let Some(config) = self.storage.load_one(key)? else {
            return Ok(None);
        };

        // Validate key format: must contain a single ":" giving "category:name".
        if !key.contains(':') {
            warn!("invalid key format, missing category: {}", key);
            return Ok(None);
        }

        self.cache
            .entry(key.to_string())
            .or_insert_with(|| CacheEntry::new(config.clone()));
        Ok(Some(config))
    }

    // ── Public API ───────────────────────────────────────────────────────

    /// All registered keys (triggers a `load_all` from storage on first call).
    pub fn keys(&self) -> Result<Vec<String>, OperonError> {
        for (key, cfg) in self.storage.load_all()? {
            self.cache
                .entry(key)
                .or_insert_with(|| CacheEntry::new(cfg));
        }
        Ok(self.cache.iter().map(|r| r.key().clone()).collect())
    }

    /// `true` if `key` exists in the hub (triggers a load on miss).
    pub fn has(&self, key: &str) -> Result<bool, OperonError> {
        if self.cache.contains_key(key) {
            return Ok(true);
        }
        Ok(self.load_config(key)?.is_some())
    }

    /// Get the instantiated resource for `key`.
    ///
    /// Lazy: on first access, loads the config via storage and dispatches
    /// through the global [`ConfigRegistry`] to build the instance.
    pub fn get(&self, key: &str) -> Result<ResourceInstance, OperonError> {
        // Fast path: instance already cached.
        if let Some(entry) = self.cache.get(key) {
            if let Some(inst) = &entry.instance {
                let inst = inst.clone();
                drop(entry);
                refresh_keycloak(&inst);
                return Ok(inst);
            }
        }

        // Slow path: acquire lock, double-check, build.
        let _guard = self.load_lock.lock();
        if let Some(entry) = self.cache.get(key) {
            if let Some(inst) = &entry.instance {
                return Ok(inst.clone());
            }
        }

        let config = self
            .load_config(key)?
            .ok_or_else(|| OperonError::ResourceHub(self.not_found_message(key)))?;

        // Category is the prefix before ":".
        let category = key
            .split(':')
            .next()
            .filter(|c| !c.is_empty())
            .ok_or_else(|| {
                OperonError::ResourceHub(format!("invalid key '{}' — missing category", key))
            })?;

        // Resolve keycloak refs in api_key, if present.
        let resolved_config = resolve_keycloak(self, &config)?;
        let create_config = resolved_config.clone().unwrap_or_else(|| config.clone());

        let instance = registry().create(category, create_config).map_err(|e| {
            warn!("failed to create resource '{}': {}", key, e);
            OperonError::ResourceHub(format!("resource '{}' failed to initialize: {}", key, e))
        })?;

        self.cache
            .entry(key.to_string())
            .and_modify(|e| e.instance = Some(instance.clone()))
            .or_insert_with(|| CacheEntry {
                config: config.clone(),
                instance: Some(instance.clone()),
            });
        debug!("lazy loaded resource: {}", key);
        Ok(instance)
    }

    /// Fetch the config dict for `key` without materializing the instance.
    pub fn get_config(&self, key: &str) -> Result<ConfigDict, OperonError> {
        self.load_config(key)?
            .ok_or_else(|| OperonError::ResourceHub(self.not_found_message(key)))
    }

    /// Build a 'not found' error string with available keys + source path.
    ///
    /// Disambiguates "key absent" from "hub unconfigured" — the source path
    /// proves the hub *is* loaded, the available list shows what *is*
    /// reachable, so the user knows it's a typo and where to fix it.
    fn not_found_message(&self, key: &str) -> String {
        let available: Vec<String> = match self.storage.load_all() {
            Ok(m) => {
                let mut v: Vec<String> = m.keys().cloned().collect();
                v.sort();
                v
            }
            Err(_) => {
                let mut v: Vec<String> = self.cache.iter().map(|r| r.key().clone()).collect();
                v.sort();
                v
            }
        };
        let source = self
            .source_path
            .as_ref()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|| "<in-memory storage>".to_string());
        if available.is_empty() {
            format!(
                "Resource '{}' not found in {}.\n  (No resources loaded — file may be empty.)",
                key, source
            )
        } else {
            let avail = available
                .iter()
                .map(|k| format!("'{}'", k))
                .collect::<Vec<_>>()
                .join(", ");
            format!(
                "Resource '{}' not found in {}.\n  Available: [{}]",
                key, source, avail
            )
        }
    }

    /// Register a new config in-memory + persist to storage.
    ///
    /// `category` drives the factory lookup. `registry_key` is auto-derived
    /// as `category:<name-or-hash>` if not provided.
    pub fn register(
        &self,
        category: &str,
        config: ConfigDict,
        registry_key: Option<String>,
    ) -> Result<String, OperonError> {
        let key = registry_key.unwrap_or_else(|| key_of(category, &config));
        let instance = registry().create(category, config.clone())?;
        self.cache.insert(
            key.clone(),
            CacheEntry {
                config: config.clone(),
                instance: Some(instance),
            },
        );
        self.storage.save(&key, config)?;
        debug!("registered: {}", key);
        Ok(key)
    }

    /// Remove a resource from cache + storage. Returns `true` if it existed.
    pub fn remove(&self, key: &str) -> Result<bool, OperonError> {
        let existed_in_cache = self.cache.remove(key).is_some();
        let existed_in_storage = self.storage.remove(key)?;
        let existed = existed_in_cache || existed_in_storage;
        if existed {
            debug!("removed: {}", key);
        }
        Ok(existed)
    }

    /// Drop every cached entry and persisted config.
    pub fn clear(&self) -> Result<(), OperonError> {
        let keys: Vec<String> = self.cache.iter().map(|r| r.key().clone()).collect();
        self.cache.clear();
        for key in keys {
            let _ = self.storage.remove(&key);
        }
        debug!("cleared all resources");
        Ok(())
    }

    /// Close the underlying storage.
    pub fn close(&self) -> Result<(), OperonError> {
        self.storage.close()
    }

    // ── Health check ─────────────────────────────────────────────────────

    /// Try to materialize each (or only the specified) resource and report
    /// pass/fail per key.
    pub fn health_check(&self, keys: Option<&[String]>) -> HealthCheckResult {
        let check_keys: Vec<String> = match keys {
            Some(k) => k.to_vec(),
            None => self.keys().unwrap_or_default(),
        };

        let mut results = std::collections::HashMap::new();
        let mut errors = std::collections::HashMap::new();

        for key in check_keys {
            match self.get(&key) {
                Ok(_) => {
                    results.insert(key, true);
                }
                Err(e) => {
                    warn!("health check failed for '{}': {}", key, e);
                    results.insert(key.clone(), false);
                    errors.insert(key, e.to_string());
                }
            }
        }
        HealthCheckResult { results, errors }
    }
}

// ── Global singleton plumbing ────────────────────────────────────────────

fn global() -> &'static RwLock<Option<Arc<ResourceHub>>> {
    use std::sync::OnceLock;
    static G: OnceLock<RwLock<Option<Arc<ResourceHub>>>> = OnceLock::new();
    G.get_or_init(|| RwLock::new(None))
}

/// Backing storage for [`ResourceHub::empty`] — every call is a miss.
#[derive(Debug)]
struct EmptyStorage;

impl ConfigStorage for EmptyStorage {
    fn load_one(&self, _key: &str) -> Result<Option<ConfigDict>, OperonError> {
        Ok(None)
    }

    fn load_all(&self) -> Result<std::collections::HashMap<String, ConfigDict>, OperonError> {
        Ok(std::collections::HashMap::new())
    }

    fn save(&self, _key: &str, _config: ConfigDict) -> Result<bool, OperonError> {
        Err(OperonError::ResourceHub(
            "empty ResourceHub: writes disabled".into(),
        ))
    }

    fn remove(&self, _key: &str) -> Result<bool, OperonError> {
        Ok(false)
    }
}

// ── Keycloak token resolution ────────────────────────────────────────────
//
// Real implementation lands when `providers/auth/keycloak.rs` is ported
// in Phase 5. Until then: no-op passthrough that returns the config
// unchanged when no `keycloak:` reference is present, or errors loudly if
// one *is* present (so we don't silently drop the reference).

fn refresh_keycloak(_instance: &ResourceInstance) {
    // TODO(phase-5): downcast to a provider trait exposing `refresh_keycloak_token`.
}

fn resolve_keycloak(
    _hub: &ResourceHub,
    config: &ConfigDict,
) -> Result<Option<ConfigDict>, OperonError> {
    if let Some(serde_json::Value::String(api_key)) = config.get("api_key") {
        if let Some(name) = api_key.strip_prefix("keycloak:") {
            return Err(OperonError::ResourceHub(format!(
                "keycloak reference 'keycloak:{}' in api_key — keycloak provider \
                 not yet implemented in Rust backend (Phase 5)",
                name
            )));
        }
    }
    Ok(None)
}

// ── Key derivation ───────────────────────────────────────────────────────

fn key_of(category: &str, config: &ConfigDict) -> String {
    // Prefer `model`, then `name`; fall back to a short hash of the config.
    if let Some(model) = config.get("model").and_then(|v| v.as_str()) {
        return format!("{}:{}", category, model);
    }
    if let Some(name) = config.get("name").and_then(|v| v.as_str()) {
        return format!("{}:{}", category, name);
    }
    format!("{}:{}", category, hash_of(config))
}

fn hash_of(config: &ConfigDict) -> String {
    // Matches Python's "md5[:8]" truncation — uses a non-cryptographic hash
    // here since the value is only used as a stable key suffix.
    let raw = serde_json::to_string(config).unwrap_or_default();
    let hash = fnv1a(&raw);
    format!("{:08x}", hash)
}

/// 32-bit FNV-1a — enough entropy for an 8-char hex tag, zero deps.
fn fnv1a(input: &str) -> u32 {
    let mut hash: u32 = 0x811c_9dc5;
    for b in input.bytes() {
        hash ^= u32::from(b);
        hash = hash.wrapping_mul(0x0100_0193);
    }
    hash
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::registry::config_registry::{registry, Factory};
    use std::sync::Arc;

    #[derive(Debug)]
    struct FakeResource {
        value: String,
    }

    fn register_fake_once() {
        // Register once per test process — collisions raise, so swallow them.
        let factory: Factory = Arc::new(|cfg: ConfigDict| {
            let v = cfg
                .get("value")
                .and_then(|v| v.as_str())
                .unwrap_or("default")
                .to_string();
            Ok(Arc::new(FakeResource { value: v }) as ResourceInstance)
        });
        let _ = registry().register("testfake", factory, Some("FakeConfig"));
    }

    #[test]
    fn instance_errors_before_set() {
        ResourceHub::reset_instance();
        let err = ResourceHub::instance().unwrap_err();
        assert!(matches!(err, OperonError::ResourceHub(_)));
    }

    #[test]
    fn get_from_cache_roundtrip() {
        register_fake_once();

        // Build a minimal hub with an in-memory storage stub (no backing file).
        struct MemStorage {
            data: std::sync::Mutex<std::collections::HashMap<String, ConfigDict>>,
        }
        impl ConfigStorage for MemStorage {
            fn load_one(&self, key: &str) -> Result<Option<ConfigDict>, OperonError> {
                Ok(self.data.lock().unwrap().get(key).cloned())
            }
            fn load_all(
                &self,
            ) -> Result<std::collections::HashMap<String, ConfigDict>, OperonError> {
                Ok(self.data.lock().unwrap().clone())
            }
            fn save(&self, k: &str, c: ConfigDict) -> Result<bool, OperonError> {
                self.data.lock().unwrap().insert(k.to_string(), c);
                Ok(true)
            }
            fn remove(&self, k: &str) -> Result<bool, OperonError> {
                Ok(self.data.lock().unwrap().remove(k).is_some())
            }
        }

        let mut seed = ConfigDict::new();
        seed.insert("value".into(), serde_json::json!("hello"));
        let mut data = std::collections::HashMap::new();
        data.insert("testfake:a".to_string(), seed);
        let storage = Arc::new(MemStorage {
            data: std::sync::Mutex::new(data),
        });

        let hub = ResourceHub::new(storage);
        assert!(hub.has("testfake:a").unwrap());
        let inst = hub.get("testfake:a").unwrap();
        let r = inst
            .downcast::<FakeResource>()
            .expect("downcast to FakeResource");
        assert_eq!(r.value, "hello");
    }
}
