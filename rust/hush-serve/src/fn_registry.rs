//! Closure-based op registry — register native Rust functions as ops.
//!
//! This replaces the cdylib/FFI plugin approach for users who build
//! their own binary using `hush-serve` as a library.

use std::collections::HashMap;
use std::sync::Arc;

use hush_icore::registry::OpRegistry;
use serde_json::Value;

type OpFn = Arc<dyn Fn(&Value) -> Value + Send + Sync>;
type GenFn = Arc<dyn Fn(&Value) -> Vec<Value> + Send + Sync>;

/// A registry that dispatches ops to native Rust closures.
///
/// Unlike `PluginRegistry` (cdylib/FFI), closures can capture shared state
/// (e.g., `Arc<OnnxEmbedder>`, connection pools) with full type safety.
pub struct FnRegistry {
    ops: HashMap<String, OpFn>,
    generators: HashMap<String, GenFn>,
}

impl FnRegistry {
    pub fn new() -> Self {
        FnRegistry {
            ops: HashMap::new(),
            generators: HashMap::new(),
        }
    }

    /// Register a regular op by name.
    pub fn register_op<F>(&mut self, name: &str, f: F)
    where
        F: Fn(&Value) -> Value + Send + Sync + 'static,
    {
        self.ops.insert(name.to_string(), Arc::new(f));
    }

    /// Register a generator op by name (returns `Vec<Value>`).
    pub fn register_generator<F>(&mut self, name: &str, f: F)
    where
        F: Fn(&Value) -> Vec<Value> + Send + Sync + 'static,
    {
        self.generators.insert(name.to_string(), Arc::new(f));
    }

    /// Register a generator op that returns a JSON array `Value`.
    ///
    /// This matches the cdylib convention where generators return `Value::Array`.
    /// The array is unwrapped automatically.
    pub fn register_generator_value<F>(&mut self, name: &str, f: F)
    where
        F: Fn(&Value) -> Value + Send + Sync + 'static,
    {
        self.generators.insert(
            name.to_string(),
            Arc::new(move |inputs: &Value| match f(inputs) {
                Value::Array(items) => items,
                other => vec![other],
            }),
        );
    }

    /// Collect all `#[hush_op]`-annotated functions discovered by `inventory`.
    ///
    /// This populates the registry with all auto-registered ops found at link time.
    /// Manual `.register_op()` calls can override these (called after `auto_register()`).
    pub fn collect_from_inventory(&mut self) {
        for entry in inventory::iter::<crate::OpEntry> {
            match entry.kind {
                crate::OpKind::Regular => {
                    let f = entry.op_fn;
                    if self.ops.contains_key(entry.name) {
                        log::warn!(
                            "Auto-register: duplicate op '{}', keeping first registration",
                            entry.name
                        );
                    } else {
                        self.ops.insert(entry.name.to_string(), Arc::new(move |v| f(v)));
                    }
                }
                crate::OpKind::Generator => {
                    let f = entry.gen_fn;
                    if self.generators.contains_key(entry.name) {
                        log::warn!(
                            "Auto-register: duplicate generator '{}', keeping first registration",
                            entry.name
                        );
                    } else {
                        self.generators.insert(
                            entry.name.to_string(),
                            Arc::new(move |v| match f(v) {
                                Value::Array(items) => items,
                                other => vec![other],
                            }),
                        );
                    }
                }
            }
        }
    }

    /// Returns true if no ops or generators are registered.
    pub fn is_empty(&self) -> bool {
        self.ops.is_empty() && self.generators.is_empty()
    }
}

impl Default for FnRegistry {
    fn default() -> Self {
        Self::new()
    }
}

impl OpRegistry for FnRegistry {
    fn call(&self, name: &str, inputs: &Value) -> Option<Value> {
        self.ops.get(name).map(|f| f(inputs))
    }

    fn call_generator(&self, name: &str, inputs: &Value) -> Option<Vec<Value>> {
        self.generators.get(name).map(|f| f(inputs))
    }
}

/// A registry that tries a primary registry first, then falls back to a secondary.
///
/// Used to combine `FnRegistry` (compiled-in ops) with `PluginRegistry` (cdylib)
/// for backward compatibility.
pub struct CompositeRegistry {
    primary: Arc<dyn OpRegistry>,
    fallback: Arc<dyn OpRegistry>,
}

impl CompositeRegistry {
    pub fn new(primary: Arc<dyn OpRegistry>, fallback: Arc<dyn OpRegistry>) -> Self {
        CompositeRegistry { primary, fallback }
    }
}

impl OpRegistry for CompositeRegistry {
    fn call(&self, name: &str, inputs: &Value) -> Option<Value> {
        self.primary
            .call(name, inputs)
            .or_else(|| self.fallback.call(name, inputs))
    }

    fn call_generator(&self, name: &str, inputs: &Value) -> Option<Vec<Value>> {
        self.primary
            .call_generator(name, inputs)
            .or_else(|| self.fallback.call_generator(name, inputs))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_fn_registry_call() {
        let mut reg = FnRegistry::new();
        reg.register_op("double", |v| {
            let x = v["x"].as_i64().unwrap_or(0);
            json!({"result": x * 2})
        });

        let result = reg.call("double", &json!({"x": 5}));
        assert_eq!(result, Some(json!({"result": 10})));
        assert_eq!(reg.call("unknown", &json!({})), None);
    }

    #[test]
    fn test_fn_registry_generator() {
        let mut reg = FnRegistry::new();
        reg.register_generator("range", |v| {
            let n = v["n"].as_i64().unwrap_or(0);
            (0..n).map(|i| json!({"value": i})).collect()
        });

        let result = reg.call_generator("range", &json!({"n": 3}));
        assert_eq!(
            result,
            Some(vec![
                json!({"value": 0}),
                json!({"value": 1}),
                json!({"value": 2}),
            ])
        );
    }

    #[test]
    fn test_fn_registry_shared_state() {
        let counter = Arc::new(std::sync::atomic::AtomicI64::new(0));
        let c = counter.clone();

        let mut reg = FnRegistry::new();
        reg.register_op("increment", move |_| {
            let val = c.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            json!({"count": val + 1})
        });

        reg.call("increment", &json!({}));
        reg.call("increment", &json!({}));
        let result = reg.call("increment", &json!({}));
        assert_eq!(result, Some(json!({"count": 3})));
    }

    #[test]
    fn test_composite_registry() {
        let mut primary = FnRegistry::new();
        primary.register_op("a", |_| json!({"from": "primary"}));

        let mut fallback = FnRegistry::new();
        fallback.register_op("a", |_| json!({"from": "fallback"}));
        fallback.register_op("b", |_| json!({"from": "fallback"}));

        let composite = CompositeRegistry::new(Arc::new(primary), Arc::new(fallback));

        // primary wins for "a"
        assert_eq!(composite.call("a", &json!({})), Some(json!({"from": "primary"})));
        // fallback for "b"
        assert_eq!(composite.call("b", &json!({})), Some(json!({"from": "fallback"})));
        // unknown returns None
        assert_eq!(composite.call("c", &json!({})), None);
    }
}
