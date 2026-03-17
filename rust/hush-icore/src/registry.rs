//! Op registry — trait for external op dispatch.
//!
//! Consumers (hush-serve, tests) implement this trait to provide
//! custom Rust op functions. hush-icore calls the registry at runtime
//! to resolve func_name to actual function calls.

use serde_json::Value;

/// External op provider — implements dispatch for custom Rust ops.
///
/// The registry resolves function names (matching Python @op function names)
/// to Rust function calls. Auto-registered via `#[hush_op]` attribute.
pub trait OpRegistry: Send + Sync {
    /// Call a regular op by name. Returns `Some(result)` if found, `None` if unknown.
    fn call(&self, name: &str, inputs: &Value) -> Option<Value>;

    /// Call a generator op by name. Returns `Some(Vec<Value>)` if found, `None` if unknown.
    fn call_generator(&self, name: &str, inputs: &Value) -> Option<Vec<Value>>;

    /// List all registered op names (for error messages).
    fn available_ops(&self) -> Vec<String> {
        vec![]
    }
}
