//! Custom error types for the rush-core engine.
//!
//! All errors during GIL-free execution use `RushError`.
//! Converted to `PyErr` only at the boundary in `Rush::run()`.

use pyo3::PyErr;

/// Unified error type for all rush-core operations.
///
/// Replaces `PyResult<T>` / `PyErr` throughout the engine internals.
/// Only converted to `PyErr` at the Python boundary (`engine.rs`).
#[derive(Debug, thiserror::Error)]
pub enum RushError {
    #[error("Op '{0}' has no Rust implementation")]
    UnsupportedOp(String),

    #[error("Ref resolution failed: {0}")]
    RefError(String),

    #[error("Provider error: {0}")]
    ProviderError(String),

    #[error("Plugin error: {0}")]
    PluginError(String),

    #[error("Config error: {0}")]
    ConfigError(String),

    #[error("Iteration error: {0}")]
    IterationError(String),

    #[error("Branch error: {0}")]
    BranchError(String),

    #[error("{0}")]
    Runtime(String),
}

impl From<RushError> for PyErr {
    fn from(err: RushError) -> PyErr {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(err.to_string())
    }
}

impl From<serde_json::Error> for RushError {
    fn from(err: serde_json::Error) -> Self {
        RushError::ConfigError(err.to_string())
    }
}
