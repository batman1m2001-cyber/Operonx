//! Custom error types for the rush-core engine.

/// Unified error type for all rush-core operations.
#[derive(Debug, thiserror::Error)]
pub enum RushError {
    #[error("Op '{0}' has no Rust implementation")]
    UnsupportedOp(String),

    #[error("Ref resolution failed: {0}")]
    RefError(String),

    #[error("Provider error: {0}")]
    ProviderError(String),

    #[error("Builtin op error: {0}")]
    BuiltinOpError(String),

    #[error("Config error: {0}")]
    ConfigError(String),

    #[error("Loop error: {0}")]
    LoopError(String),

    #[error("Branch error: {0}")]
    BranchError(String),

    #[error("{0}")]
    Runtime(String),
}

impl From<serde_json::Error> for RushError {
    fn from(err: serde_json::Error) -> Self {
        RushError::ConfigError(err.to_string())
    }
}
