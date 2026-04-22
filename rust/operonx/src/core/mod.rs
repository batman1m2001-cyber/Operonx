//! Core — engine, ops, state, refs, registry, tracing, middleware.
//!
//! Mirrors Python `operon/core/`.

pub mod configs;
pub mod engine;
pub mod exceptions;
pub mod loggings;
pub mod media;
pub mod middleware;
pub mod ops;
pub mod registry;
pub mod states;
pub mod tracing;
pub mod utils;

// Rust-only module (no Python counterpart): tokio runtime singleton.
pub(crate) mod runtime;
