//! Global tokio runtime — singleton for async scheduling.
//!
//! Provides a persistent multi-thread tokio runtime for:
//! - `spawn_blocking`: Python ops (each acquires GIL independently)
//! - `tokio::spawn`: future native Rust HTTP ops (no GIL at all)
//!
//! Using OnceLock ensures the runtime is created once and reused across
//! all Rush.run() calls, preserving connection pools and thread caches.

use std::sync::OnceLock;
use tokio::runtime::Runtime;

static TOKIO_RT: OnceLock<Runtime> = OnceLock::new();

/// Get or create the global tokio runtime.
pub(crate) fn get_runtime() -> &'static Runtime {
    TOKIO_RT.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("Failed to create Tokio runtime")
    })
}
