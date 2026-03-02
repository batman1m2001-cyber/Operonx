//! AIterOp execution — NOT SUPPORTED in rust mode v1.
//!
//! AIterOp (async streaming iteration) requires Python async iterables
//! which are not available in GIL-free Rust execution.
//! Users should use MapOp instead.
//!
//! This module is kept as a stub for the module declaration in mod.rs.
//! The actual error is returned by dispatch_op in graph_op.rs.
