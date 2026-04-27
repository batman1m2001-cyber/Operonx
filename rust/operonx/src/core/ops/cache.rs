//! Op-level cache.
//!
//! Any op can cache its inputs→outputs via the `cache` field on its config.
//! Mirrors Hush-ai `ops/cache.rs`. Port reuses FNV-1a hashing over JSON-serialized
//! inputs.
//!
//! # Phase 1 scope
//! `CacheConfig` enum + `OpCache` struct skeleton. Full file-based persistence
//! and hash key computation flesh out in Phase 4 when the scheduler/op-runner
//! actually consults the cache.

use dashmap::DashMap;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::PathBuf;

/// Cache configuration for an op — from the `cache:` field in the op config.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum CacheConfig {
    /// `cache: true` — in-memory only, no persistence.
    InMemory(bool),
    /// `cache: "path/to/file"` — in-memory + binary-file persistence.
    File(String),
}

/// Per-op cache instance.
///
/// Keyed by FNV-1a hash of JSON-serialized inputs. Values are the op's output
/// `Value` for that input set.
pub struct OpCache {
    map: DashMap<u64, Value>,
    path: Option<PathBuf>,
}

impl OpCache {
    pub fn new(config: &CacheConfig) -> Self {
        let path = match config {
            CacheConfig::InMemory(_) => None,
            CacheConfig::File(p) => Some(PathBuf::from(p)),
        };
        OpCache {
            map: DashMap::new(),
            path,
        }
    }

    /// Lookup a cached result by input hash.
    pub fn get(&self, hash: u64) -> Option<Value> {
        self.map.get(&hash).map(|v| v.value().clone())
    }

    /// Store a result under `hash`.
    pub fn insert(&self, hash: u64, value: Value) {
        self.map.insert(hash, value);
    }

    /// Number of cached entries.
    pub fn len(&self) -> usize {
        self.map.len()
    }

    /// `true` if no entries are cached.
    pub fn is_empty(&self) -> bool {
        self.map.is_empty()
    }

    /// Path for file-backed persistence (if configured).
    pub fn path(&self) -> Option<&PathBuf> {
        self.path.as_ref()
    }
}

impl std::fmt::Debug for OpCache {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("OpCache")
            .field("entries", &self.map.len())
            .field("path", &self.path)
            .finish()
    }
}
