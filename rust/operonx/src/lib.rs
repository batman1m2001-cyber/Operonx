//! # operonx
//!
//! High-performance Rust execution backend for Operon workflows.
//!
//! Mirrors the Python `operon` package at every level — folder structure,
//! type names, method names, OOP hierarchy. See [MIGRATION_rust.md](../../../MIGRATION_rust.md).
//!
//! ## Module layout
//!
//! - [`core`] — engine, ops, state, refs, registry, tracing, middleware
//! - [`providers`] — LLM, embedding, reranker, ONNX, Triton, auth
//! - [`telemetry`] — tracers and observability backends
//!
//! Phase 0 scaffold — see `MIGRATION_rust.md` §11 for phased implementation plan.

pub mod core;
pub mod providers;
pub mod telemetry;

// Re-export the proc macros from operonx-macros for ergonomic `use operonx::{op, resource, model};`
pub use operonx_macros::{model, op, resource};

// User-facing API (per MIGRATION_rust.md §3a.1).
pub use core::engine::{
    CollectMode, ExecutionHandle, FrameEvent, FrameSender, GraphEnvelope, Operon, OperonBuilder,
    Scheduler,
};
pub use core::exceptions::{OperonError, SUPPORTED_SCHEMA_VERSION};
pub use core::middleware::{Middleware, MiddlewareContext};
pub use core::registry::{
    bootstrap_env_paths, record_env_path, OpEntry, OpKind, ResourceEntry, ResourceHub,
};

// ── bootstrap() — one-line setup helper ────────────────────────────────

use std::path::{Path, PathBuf};

/// One-line setup for `.env` and [`ResourceHub`].
///
/// - When `env` is `true` (default), load `./.env` from CWD via `dotenvy`
///   (non-override; existing env wins). The path is recorded in the
///   global env-paths list for later diagnostic messages.
/// - When `resources` is `Some(path)`, install the hub via
///   [`ResourceHub::from_yaml`] + [`ResourceHub::set_instance`].
/// - When `resources` is `None`, call [`ResourceHub::auto`] — which checks
///   `./resources.yaml` and emits a `tracing::warn!` on miss.
/// - Idempotent: if a hub is already installed, return it unchanged.
///
/// Returns the installed hub, or `None` if no `resources.yaml` was found
/// and none was provided. Pure-compute graphs that don't need a hub can
/// ignore the return value.
pub fn bootstrap(opts: BootstrapOpts) -> Option<std::sync::Arc<ResourceHub>> {
    if opts.env {
        load_env_into_bootstrap();
    }

    if let Ok(g) = ResourceHub::instance() {
        return Some(g);
    }

    if let Some(path) = opts.resources.as_ref() {
        match ResourceHub::from_yaml(path) {
            Ok(hub) => {
                let arc = std::sync::Arc::new(hub);
                ResourceHub::set_instance(arc.clone());
                return Some(arc);
            }
            Err(e) => {
                tracing::warn!(
                    "operonx::bootstrap: failed to load resources.yaml at {}: {}",
                    path.display(),
                    e
                );
                return None;
            }
        }
    }

    ResourceHub::auto()
}

/// Options passed to [`bootstrap`]. Defaults: `env=true`, `resources=None`.
#[derive(Debug, Clone, Default)]
pub struct BootstrapOpts {
    /// Path to a `resources.yaml` to load. `None` falls back to `auto()`.
    pub resources: Option<PathBuf>,
    /// Load `./.env` via `dotenvy` (non-override).
    pub env: bool,
}

impl BootstrapOpts {
    /// Default: load `.env`, autodetect `resources.yaml`.
    pub fn new() -> Self {
        Self {
            resources: None,
            env: true,
        }
    }

    /// Set an explicit resources.yaml path.
    pub fn resources(mut self, path: impl Into<PathBuf>) -> Self {
        self.resources = Some(path.into());
        self
    }

    /// Skip `.env` loading.
    pub fn no_env(mut self) -> Self {
        self.env = false;
        self
    }
}

fn load_env_into_bootstrap() {
    let cwd = match std::env::current_dir() {
        Ok(p) => p,
        Err(_) => return,
    };
    let env_path = cwd.join(".env");
    record_env_path(env_path.clone());
    if !env_path.exists() {
        return;
    }
    if let Err(e) = dotenvy::from_path(&env_path) {
        tracing::warn!(
            ".env present but failed to load ({}): {}",
            env_path.display(),
            e
        );
    }
}

/// Path to `./resources.yaml` resolved from CWD — exposed so callers
/// (e.g. test fixtures, tooling) can pass a stable absolute path to
/// [`bootstrap`] when CWD changes mid-process.
pub fn cwd_resources_yaml() -> PathBuf {
    std::env::current_dir()
        .map(|p| p.join("resources.yaml"))
        .unwrap_or_else(|_| PathBuf::from("resources.yaml"))
}

// Avoid a "Path imported but unused" warning when cwd_resources_yaml
// is the only consumer of `Path` in this file.
#[allow(dead_code)]
fn _path_use(_: &Path) {}
