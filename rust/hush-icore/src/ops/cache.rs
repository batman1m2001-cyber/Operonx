//! Op-level caching — any op can cache inputs→outputs.
//!
//! Cache is keyed by FNV-1a hash of JSON-serialized inputs.
//! Supports in-memory only (`cache: true`) or memory + binary file (`cache: "path"`).

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use dashmap::DashMap;
use serde_json::Value;

/// Cache configuration parsed from op JSON.
#[derive(Debug, Clone)]
pub enum CacheConfig {
    /// In-memory only (no persistence).
    Memory,
    /// In-memory + binary file persistence.
    File(String),
}

/// Per-op cache instance.
pub struct OpCache {
    map: DashMap<u64, Value>,
    path: Option<PathBuf>,
}

impl OpCache {
    pub fn new(config: &CacheConfig) -> Self {
        let path = match config {
            CacheConfig::Memory => None,
            CacheConfig::File(p) => Some(PathBuf::from(p)),
        };

        let map = if let Some(ref p) = path {
            Self::load_from_file(p)
        } else {
            DashMap::new()
        };

        OpCache { map, path }
    }

    /// Check cache for a hit.
    pub fn get(&self, hash: u64) -> Option<Value> {
        self.map.get(&hash).map(|v| v.value().clone())
    }

    /// Store a result in cache.
    pub fn insert(&self, hash: u64, value: Value) {
        self.map.insert(hash, value);
    }

    /// Number of cached entries.
    pub fn len(&self) -> usize {
        self.map.len()
    }

    /// Save cache to binary file. Returns number of entries written.
    pub fn save(&self) -> Result<usize, String> {
        let path = match &self.path {
            Some(p) => p,
            None => return Ok(0),
        };

        // Ensure parent directory exists
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("Failed to create cache dir: {}", e))?;
        }

        let mut buf = Vec::new();
        let count = self.map.len() as u64;
        buf.extend_from_slice(&count.to_le_bytes());

        for entry in self.map.iter() {
            let hash = *entry.key();
            let json_bytes = serde_json::to_vec(entry.value())
                .map_err(|e| format!("Failed to serialize cache entry: {}", e))?;
            let json_len = json_bytes.len() as u32;

            buf.extend_from_slice(&hash.to_le_bytes());
            buf.extend_from_slice(&json_len.to_le_bytes());
            buf.extend_from_slice(&json_bytes);
        }

        std::fs::write(path, &buf)
            .map_err(|e| format!("Failed to write cache file: {}", e))?;

        Ok(count as usize)
    }

    /// Load cache from binary file.
    fn load_from_file(path: &PathBuf) -> DashMap<u64, Value> {
        let map = DashMap::new();

        let data = match std::fs::read(path) {
            Ok(d) => d,
            Err(_) => return map, // File doesn't exist yet — empty cache
        };

        if data.len() < 8 {
            return map;
        }

        let count = u64::from_le_bytes(data[0..8].try_into().unwrap()) as usize;
        let mut offset = 8;

        for _ in 0..count {
            if offset + 12 > data.len() {
                break;
            }

            let hash = u64::from_le_bytes(data[offset..offset + 8].try_into().unwrap());
            offset += 8;

            let json_len = u32::from_le_bytes(data[offset..offset + 4].try_into().unwrap()) as usize;
            offset += 4;

            if offset + json_len > data.len() {
                break;
            }

            if let Ok(value) = serde_json::from_slice(&data[offset..offset + json_len]) {
                map.insert(hash, value);
            }
            offset += json_len;
        }

        log::info!("Loaded {} cache entries from {}", map.len(), path.display());
        map
    }
}

/// Global cache store — keyed by op full_name.
/// Shared across requests via Arc.
pub struct CacheStore {
    caches: DashMap<String, Arc<OpCache>>,
    configs: HashMap<String, CacheConfig>,
}

impl CacheStore {
    pub fn new() -> Self {
        CacheStore {
            caches: DashMap::new(),
            configs: HashMap::new(),
        }
    }

    /// Register a cache config for an op. Called during config parsing.
    pub fn register(&mut self, op_full_name: &str, config: CacheConfig) {
        self.configs.insert(op_full_name.to_string(), config);
    }

    /// Get or create cache for an op.
    pub fn get_cache(&self, op_full_name: &str) -> Option<Arc<OpCache>> {
        // Fast path: already initialized
        if let Some(cache) = self.caches.get(op_full_name) {
            return Some(cache.value().clone());
        }

        // Slow path: check config and initialize
        if let Some(config) = self.configs.get(op_full_name) {
            let cache = Arc::new(OpCache::new(config));
            self.caches.insert(op_full_name.to_string(), cache.clone());
            Some(cache)
        } else {
            None
        }
    }

    /// Save all file-backed caches. Returns total entries saved.
    pub fn save_all(&self) -> usize {
        let mut total = 0;
        for entry in self.caches.iter() {
            match entry.value().save() {
                Ok(n) => total += n,
                Err(e) => log::error!("Failed to save cache for {}: {}", entry.key(), e),
            }
        }
        total
    }
}

/// FNV-1a hash for input map.
pub fn hash_inputs(inputs: &serde_json::Map<String, Value>) -> u64 {
    // Serialize to canonical JSON (serde_json sorts keys in Map)
    let bytes = serde_json::to_vec(inputs).unwrap_or_default();
    fnv1a(&bytes)
}

fn fnv1a(data: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf29ce484222325;
    for &byte in data {
        hash ^= byte as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

// --- Global cache store ---

use std::sync::OnceLock;

static GLOBAL_STORE: OnceLock<CacheStore> = OnceLock::new();

/// Initialize the global cache store from a graph config.
/// Called once per process (in hush-serve) or per engine (standalone).
pub fn init_global_store(store: CacheStore) {
    let _ = GLOBAL_STORE.set(store);
}

/// Get or create cache for an op from the global store.
pub fn get_op_cache(op_full_name: &str) -> Option<Arc<OpCache>> {
    GLOBAL_STORE.get().and_then(|s| s.get_cache(op_full_name))
}

/// Save all file-backed caches in the global store.
pub fn save_all_caches() -> usize {
    GLOBAL_STORE.get().map_or(0, |s| s.save_all())
}

/// Build a CacheStore from a graph config by scanning all ops for cache settings.
pub fn build_store_from_config(config: &crate::config::GraphConfig) -> CacheStore {
    let mut store = CacheStore::new();
    register_ops_recursive(&mut store, &config.ops);
    store
}

fn register_ops_recursive(store: &mut CacheStore, ops: &ahash::AHashMap<String, crate::config::OpConfig>) {
    for op in ops.values() {
        if let Some(ref cache_config) = op.cache {
            store.register(&op.full_name, cache_config.clone());
        }
        // Recurse into nested graphs
        if let Some(ref inner) = op.inner_graph {
            register_ops_recursive(store, &inner.ops);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_hash_deterministic() {
        let mut map = serde_json::Map::new();
        map.insert("x".to_string(), json!(42));
        map.insert("y".to_string(), json!("hello"));

        let h1 = hash_inputs(&map);
        let h2 = hash_inputs(&map);
        assert_eq!(h1, h2);
    }

    #[test]
    fn test_hash_different_inputs() {
        let mut m1 = serde_json::Map::new();
        m1.insert("x".to_string(), json!(1));

        let mut m2 = serde_json::Map::new();
        m2.insert("x".to_string(), json!(2));

        assert_ne!(hash_inputs(&m1), hash_inputs(&m2));
    }

    #[test]
    fn test_opcache_memory() {
        let cache = OpCache::new(&CacheConfig::Memory);
        assert_eq!(cache.len(), 0);

        cache.insert(123, json!({"result": 42}));
        assert_eq!(cache.len(), 1);
        assert_eq!(cache.get(123), Some(json!({"result": 42})));
        assert_eq!(cache.get(456), None);
    }

    #[test]
    fn test_cache_store() {
        let mut store = CacheStore::new();
        store.register("test.op", CacheConfig::Memory);

        let cache = store.get_cache("test.op").unwrap();
        cache.insert(1, json!({"a": 1}));
        assert_eq!(cache.get(1), Some(json!({"a": 1})));

        // Non-registered op returns None
        assert!(store.get_cache("other.op").is_none());
    }
}
