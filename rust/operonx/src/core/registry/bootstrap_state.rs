//! Shared state populated by [`crate::bootstrap`] and consumed by
//! [`crate::core::registry::storage::yaml`] / [`crate::core::registry::resource_hub`]
//! when surfacing setup-related warnings and errors.
//!
//! Mirrors Python's module-level `BOOTSTRAP_ENV_PATHS` list in
//! `operon/core/registry/errors.py`.

use std::path::PathBuf;
use std::sync::{OnceLock, RwLock};

fn paths() -> &'static RwLock<Vec<PathBuf>> {
    static G: OnceLock<RwLock<Vec<PathBuf>>> = OnceLock::new();
    G.get_or_init(|| RwLock::new(Vec::new()))
}

/// Append `path` to the global `.env`-paths list. Idempotent on the same path.
pub fn record_env_path(path: PathBuf) {
    let mut g = paths().write().expect("BOOTSTRAP_ENV_PATHS poisoned");
    if !g.iter().any(|p| p == &path) {
        g.push(path);
    }
}

/// Snapshot of every `.env` path that [`crate::bootstrap`] has tried to load.
pub fn env_paths() -> Vec<PathBuf> {
    paths().read().expect("BOOTSTRAP_ENV_PATHS poisoned").clone()
}

/// Clear the path list — tests only.
pub fn reset_env_paths() {
    paths().write().expect("BOOTSTRAP_ENV_PATHS poisoned").clear();
}
