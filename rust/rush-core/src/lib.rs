//! rush-core — Pure Rust execution engine for Hush workflows.
//!
//! No PyO3 dependency. Communicates via JSON:
//!   - Input: JSON config string from GraphOp.serialize()
//!   - Input: JSON inputs (serde_json::Value)
//!   - Output: JSON result (serde_json::Value)

pub mod builtin_ops;
pub mod middleware;
pub mod config;
pub mod engine;
pub mod error;
pub mod ops;
pub mod refs;
pub mod runtime;
pub mod states;
pub mod tracing;
