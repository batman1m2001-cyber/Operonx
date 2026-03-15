//! hush-serve — build Hush workflow HTTP servers in Rust.
//!
//! Use as a **library** to build custom binaries with compiled-in ops:
//!
//! ```rust,ignore
//! use hush_serve::HushServer;
//! use serde_json::{json, Value};
//! use std::sync::Arc;
//!
//! fn double(inputs: &Value) -> Value {
//!     let x = inputs["x"].as_i64().unwrap_or(0);
//!     json!({"result": x * 2})
//! }
//!
//! fn main() {
//!     HushServer::builder()
//!         .register_op("double", double)
//!         .from_cli()
//!         .serve()
//!         .unwrap();
//! }
//! ```
//!
//! Or use the convenience macro for many ops:
//!
//! ```rust,ignore
//! mod math;
//! mod text;
//!
//! fn main() {
//!     hush_ops!(HushServer::builder();
//!         ops: {
//!             "double" => math::double,
//!             "greet" => text::greet,
//!         },
//!         generators: {
//!             "each_item" => iteration::each_item,
//!         }
//!     ).from_cli().serve().unwrap();
//! }
//! ```
//!
//! The crate also ships a default **binary** (`hush-serve`) for workflows
//! that only use built-in ops (LLM, embedding, reranking).

pub mod builder;
pub mod config;
pub mod error;
pub mod fn_registry;

pub(crate) mod execute;
pub(crate) mod plugin;
pub(crate) mod router;
pub(crate) mod routes;
pub(crate) mod state;

pub use builder::HushServerBuilder;
pub use fn_registry::FnRegistry;

// Re-export types users commonly need
pub use hush_icore::registry::OpRegistry;
pub use serde_json::Value;

/// Entry point for building a Hush HTTP server.
pub struct HushServer;

impl HushServer {
    /// Create a new server builder.
    pub fn builder() -> HushServerBuilder {
        HushServerBuilder::new()
    }
}

/// Register multiple ops and generators on a builder in one shot.
///
/// Mirrors the `hush_plugin!` macro syntax for easy migration from cdylib plugins.
///
/// # Example
///
/// ```rust,ignore
/// use hush_serve::{HushServer, hush_ops};
///
/// hush_ops!(HushServer::builder();
///     ops: {
///         "double" => math::double,
///         "add" => math::add,
///     },
///     generators: {
///         "each_item" => iteration::each_item,
///     }
/// ).from_cli().serve().unwrap();
/// ```
#[macro_export]
macro_rules! hush_ops {
    // ops + generators
    (
        $builder:expr;
        ops: { $($name:literal => $func:expr),* $(,)? },
        generators: { $($gname:literal => $gfunc:expr),* $(,)? }
    ) => {{
        let mut b = $builder;
        $(b = b.register_op($name, $func);)*
        $(b = b.register_generator_value($gname, $gfunc);)*
        b
    }};
    // ops only
    (
        $builder:expr;
        ops: { $($name:literal => $func:expr),* $(,)? }
    ) => {{
        let mut b = $builder;
        $(b = b.register_op($name, $func);)*
        b
    }};
}
