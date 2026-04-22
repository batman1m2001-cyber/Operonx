//! Unified exception types for Operon.
//!
//! Mirrors Python `operon/core/exceptions.py`:
//! - [`OpError`] — op-level error hierarchy (Parser / Code / Branch / Condition /
//!   Iteration / Prompt / Embedding / Rerank), matching Python's `OpError` subclasses.
//! - [`OperonError`] — top-level error type wrapping `OpError` plus engine-level
//!   categories (Provider, ResourceHub, Config, State, Runtime).
//!
//! Python's subclass hierarchy collapses to Rust enum variants here (see
//! MIGRATION_rust.md §3a.6). `matches!(err, OpError::Parser(_))` replaces
//! `isinstance(err, ParserError)`.

use std::error::Error as StdError;

/// Op-level error categories, mirroring Python's `OpError` subclass hierarchy.
#[derive(Debug, thiserror::Error)]
pub enum OpError {
    #[error("[PARSER] {0}")]
    Parser(String),
    #[error("[CODE] {0}")]
    Code(String),
    #[error("[BRANCH] {0}")]
    Branch(String),
    #[error("[WHILE] {0}")]
    Condition(String),
    #[error("[FOR] {0}")]
    Iteration(String),
    #[error("[PROMPT] {0}")]
    Prompt(String),
    #[error("[EMBEDDING] {0}")]
    Embedding(String),
    #[error("[RERANK] {0}")]
    Rerank(String),
}

/// Top-level error type for all Operon operations.
///
/// Wraps [`OpError`] for op-level failures and adds engine-level categories.
#[derive(Debug, thiserror::Error)]
pub enum OperonError {
    #[error(transparent)]
    Op(#[from] OpError),

    #[error("provider error: {0}")]
    Provider(String),

    #[error("resource hub: {0}")]
    ResourceHub(String),

    #[error("config: {0}")]
    Config(String),

    #[error("state: {0}")]
    State(String),

    #[error("runtime: {0}")]
    Runtime(String),

    #[error("schema: unsupported schema_version {0}; expected {expected}", expected = SUPPORTED_SCHEMA_VERSION)]
    UnsupportedSchema(String),
}

/// The serialized graph schema version Rust accepts. Bump on breaking changes.
pub const SUPPORTED_SCHEMA_VERSION: &str = "1.0";

// ── From impls for common upstream errors ─────────────────────────────────

impl From<serde_json::Error> for OperonError {
    fn from(e: serde_json::Error) -> Self {
        OperonError::Config(e.to_string())
    }
}

impl From<serde_yaml::Error> for OperonError {
    fn from(e: serde_yaml::Error) -> Self {
        OperonError::Config(e.to_string())
    }
}

impl From<std::io::Error> for OperonError {
    fn from(e: std::io::Error) -> Self {
        OperonError::Runtime(e.to_string())
    }
}

/// Convenience alias for `Result<T, OperonError>`.
pub type Result<T> = std::result::Result<T, OperonError>;

/// Boxed dynamic error for cases where the source type is opaque (e.g., provider HTTP errors).
pub type BoxError = Box<dyn StdError + Send + Sync + 'static>;
