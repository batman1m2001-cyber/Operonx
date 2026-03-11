//! Global tokio runtime — singleton for async scheduling.
//!
//! Provides a persistent multi-thread tokio runtime for:
//! - `spawn_blocking`: Python ops (each acquires GIL independently)
//! - `tokio::spawn`: native Rust HTTP ops (no GIL at all)
//!
//! Using OnceLock ensures the runtime is created once and reused across
//! all Rush.run() calls, preserving connection pools and thread caches.

use std::future::Future;
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

/// Block on an async future from any context.
///
/// - If already inside the tokio runtime (e.g. `spawn_blocking`): uses
///   `Handle::block_on()` which is designed for this case.
/// - If outside tokio (e.g. main thread): uses `Runtime::block_on()`.
///
/// This avoids the "Cannot start a runtime from within a runtime" panic
/// that would occur if you called `Runtime::block_on()` from a
/// `spawn_blocking` task.
pub(crate) fn block_on_async<F: Future>(future: F) -> F::Output {
    match tokio::runtime::Handle::try_current() {
        Ok(handle) => handle.block_on(future),
        Err(_) => get_runtime().block_on(future),
    }
}
