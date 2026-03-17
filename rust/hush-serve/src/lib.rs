//! hush-serve — build Hush workflow HTTP servers in Rust.
//!
//! Use `#[hush_op]` + `.auto_register()` for zero-boilerplate op registration:
//!
//! ```rust,ignore
//! use hush_serve::{hush_op, HushServer};
//! use serde_json::{json, Value};
//!
//! #[hush_op]
//! fn double(inputs: &Value) -> Value {
//!     let x = inputs["x"].as_i64().unwrap_or(0);
//!     json!({"result": x * 2})
//! }
//!
//! fn main() {
//!     HushServer::builder()
//!         .auto_register()  // discovers all #[hush_op] functions
//!         .from_cli()
//!         .serve()
//!         .unwrap();
//! }
//! ```
//!
//! For ops that need shared resources (ONNX models, caches, pools):
//!
//! ```rust,ignore
//! use hush_serve::{hush_op, HushServer};
//!
//! #[hush_op]
//! fn classify(inputs: &Value) -> Value {
//!     let embedder = hush_serve::get::<OnnxEmbedder>();
//!     // ...
//! }
//!
//! fn main() {
//!     HushServer::builder()
//!         .provide(OnnxEmbedder::new("./models/bge-m3").unwrap())
//!         .auto_register()
//!         .from_cli()
//!         .serve()
//!         .unwrap();
//! }
//! ```
//!
//! The crate also ships a default **binary** (`hush-serve`) for workflows
//! that only use built-in ops (LLM, embedding, reranking).

pub mod builder;
pub mod config;
pub mod error;
pub mod fn_registry;
pub mod resources;

pub(crate) mod execute;
pub(crate) mod router;
pub(crate) mod routes;
pub(crate) mod state;

pub use builder::HushServerBuilder;
pub use fn_registry::FnRegistry;
pub use resources::get;

// Re-export proc macros
pub use hush_macros::hush_op;
pub use hush_macros::hush_model;
pub use hush_macros::hush_resource;

// Re-export types users commonly need
pub use hush_icore::registry::OpRegistry;
pub use serde_json::Value;

// --- Auto-registration types (used by #[hush_op] proc macro) ---

/// Whether an auto-registered op is regular or a generator.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OpKind {
    Regular,
    Generator,
}

/// An auto-registered op entry, collected by `inventory` at link time.
///
/// Created by the `#[hush_op]` proc macro — users don't construct these directly.
pub struct OpEntry {
    pub name: &'static str,
    pub module_path: &'static str,
    pub kind: OpKind,
    pub op_fn: fn(&serde_json::Value) -> serde_json::Value,
    pub gen_fn: fn(&serde_json::Value) -> serde_json::Value,
}

impl OpEntry {
    /// Create a regular op entry (used by `#[hush_op]`).
    pub const fn new_op(
        name: &'static str,
        module_path: &'static str,
        op_fn: fn(&serde_json::Value) -> serde_json::Value,
    ) -> Self {
        OpEntry {
            name,
            module_path,
            kind: OpKind::Regular,
            op_fn,
            gen_fn: _noop_gen,
        }
    }

    /// Create a generator op entry (used by `#[hush_op(generator)]`).
    pub const fn new_gen(
        name: &'static str,
        module_path: &'static str,
        gen_fn: fn(&serde_json::Value) -> serde_json::Value,
    ) -> Self {
        OpEntry {
            name,
            module_path,
            kind: OpKind::Generator,
            op_fn: _noop_op,
            gen_fn,
        }
    }

    /// Module-qualified name with crate prefix stripped.
    /// "example_ops::math" + "double" → "math::double"
    /// If module_path is just the crate name (root module), returns bare name.
    pub fn qualified_name(&self) -> String {
        let stripped = match self.module_path.find("::") {
            Some(idx) => &self.module_path[idx + 2..],
            None => "",
        };
        if stripped.is_empty() {
            self.name.to_string()
        } else {
            format!("{}::{}", stripped, self.name)
        }
    }
}

fn _noop_op(_: &serde_json::Value) -> serde_json::Value {
    serde_json::Value::Null
}

fn _noop_gen(_: &serde_json::Value) -> serde_json::Value {
    serde_json::Value::Array(vec![])
}

inventory::collect!(OpEntry);

// --- Auto-registration types for resources (used by #[hush_resource] proc macro) ---

/// An auto-registered resource factory, collected by `inventory` at link time.
///
/// Created by the `#[hush_resource]` proc macro — users don't construct these directly.
pub struct ResourceEntry {
    pub name: &'static str,
    pub factory: fn(&serde_json::Value) -> Box<dyn std::any::Any + Send + Sync>,
}

impl ResourceEntry {
    pub const fn new(
        name: &'static str,
        factory: fn(&serde_json::Value) -> Box<dyn std::any::Any + Send + Sync>,
    ) -> Self {
        ResourceEntry { name, factory }
    }
}

inventory::collect!(ResourceEntry);

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
