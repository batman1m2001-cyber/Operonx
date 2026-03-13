//! Op registry — trait for external op dispatch.
//!
//! Consumers (hush-serve, tests) implement this trait to provide
//! custom Rust op functions. hush-icore calls the registry at runtime
//! to resolve `rust_op` names to actual function calls.

use serde_json::Value;

/// External op provider — implements dispatch for custom Rust ops.
///
/// The registry resolves `rust_op` names (from `@op(rust="...")` in Python)
/// to actual Rust function calls. Names may contain module paths
/// (e.g., `"./rust_ops::analytics::analyze_text"`) — the registry receives
/// the raw name and is responsible for parsing it.
pub trait OpRegistry: Send + Sync {
    /// Call a regular op by name. Returns `Some(result)` if found, `None` if unknown.
    fn call(&self, name: &str, inputs: &Value) -> Option<Value>;

    /// Call a generator op by name. Returns `Some(Vec<Value>)` if found, `None` if unknown.
    fn call_generator(&self, name: &str, inputs: &Value) -> Option<Vec<Value>>;
}
