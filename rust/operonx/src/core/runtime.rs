//! Global tokio runtime — singleton for async scheduling.
//!
//! **Rust-internal.** No Python counterpart (Python's asyncio loop is per-thread).
//!
//! Rationale for a pinned singleton:
//! - `spawn_blocking` for `bound="cpu"` ops (no Python GIL in Rust, but keeps
//!   long-running work off tokio worker threads).
//! - `tokio::spawn` for `bound="io"` ops (HTTP, LLM streaming).
//! - Persistent runtime preserves connection pools, DNS cache, thread affinity
//!   across many `Operon::run()` calls in long-lived binaries.

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
/// - Inside a tokio runtime (e.g. `spawn_blocking`): uses `Handle::block_on()`.
/// - Outside tokio (e.g. main thread in a sync call): uses the singleton.
///
/// Avoids the "Cannot start a runtime from within a runtime" panic.
pub(crate) fn block_on_async<F: Future>(future: F) -> F::Output {
    match tokio::runtime::Handle::try_current() {
        Ok(handle) => handle.block_on(future),
        Err(_) => get_runtime().block_on(future),
    }
}
