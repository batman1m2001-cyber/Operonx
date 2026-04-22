//! `OpRegistry` — function dispatch for `code`-type ops.
//!
//! Mirrors the role of `FuncOp.code_fn` on the Python side. In Python, each
//! `@op` decorator attaches the underlying callable to the op instance and the
//! scheduler reads it off `op.core`. In Rust the serialized graph only carries
//! a `func_name` string (e.g., `"my_module.double"`) — the scheduler looks up
//! the actual Rust fn by that name here.
//!
//! # Phase 4 scope
//! Defines the [`OpRegistry`] trait plus [`InMemoryOpRegistry`] — a simple
//! `DashMap`-backed implementation for tests and manual registration. Phase 6
//! adds compile-time auto-registration via `inventory` + the `#[op]`
//! proc-macro (see `operonx-macros`).

use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

use dashmap::DashMap;
use serde_json::{Map, Value};

use crate::core::exceptions::OperonError;

/// The dispatched function shape. Accepts resolved inputs as a JSON object,
/// returns a JSON object of outputs (or an error). All ops — sync or async
/// — share this signature; sync bodies wrap their result in an `async move`.
pub type OpFunc = Arc<
    dyn Fn(Map<String, Value>) -> Pin<Box<dyn Future<Output = Result<Value, OperonError>> + Send>>
        + Send
        + Sync,
>;

/// Look up an op body by its serialized `func_name`.
pub trait OpRegistry: Send + Sync {
    /// Resolve `func_name` to a dispatchable fn, or `None` if unknown.
    fn lookup(&self, func_name: &str) -> Option<OpFunc>;
}

/// `DashMap`-backed registry for manual registration (and Phase 6's
/// auto-registration shim).
pub struct InMemoryOpRegistry {
    fns: DashMap<String, OpFunc>,
}

impl InMemoryOpRegistry {
    /// Empty registry.
    pub fn new() -> Self {
        Self {
            fns: DashMap::new(),
        }
    }

    /// Register an `async fn` under `func_name`. Accepts any `Fn` returning a
    /// `Future` of the right shape — typically produced by an `async move`
    /// block or an `async fn` directly.
    pub fn register_async<F, Fut>(&self, func_name: impl Into<String>, f: F) -> &Self
    where
        F: Fn(Map<String, Value>) -> Fut + Send + Sync + 'static,
        Fut: Future<Output = Result<Value, OperonError>> + Send + 'static,
    {
        let func: OpFunc = Arc::new(move |inputs| Box::pin(f(inputs)));
        self.fns.insert(func_name.into(), func);
        self
    }

    /// Register a **sync** Rust fn under `func_name`. The fn is wrapped so the
    /// scheduler can still `.await` it.
    pub fn register_sync<F>(&self, func_name: impl Into<String>, f: F) -> &Self
    where
        F: Fn(Map<String, Value>) -> Result<Value, OperonError> + Send + Sync + 'static,
    {
        let f = Arc::new(f);
        let func: OpFunc = Arc::new(move |inputs| {
            let f = f.clone();
            Box::pin(async move { f(inputs) })
        });
        self.fns.insert(func_name.into(), func);
        self
    }

    /// Number of registered ops.
    pub fn len(&self) -> usize {
        self.fns.len()
    }

    /// `true` when no ops are registered.
    pub fn is_empty(&self) -> bool {
        self.fns.is_empty()
    }
}

impl Default for InMemoryOpRegistry {
    fn default() -> Self {
        Self::new()
    }
}

impl OpRegistry for InMemoryOpRegistry {
    fn lookup(&self, func_name: &str) -> Option<OpFunc> {
        self.fns.get(func_name).map(|e| e.value().clone())
    }
}

impl std::fmt::Debug for InMemoryOpRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("InMemoryOpRegistry")
            .field("registered", &self.fns.len())
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[tokio::test]
    async fn register_and_dispatch_sync() {
        let reg = InMemoryOpRegistry::new();
        reg.register_sync("double", |inputs| {
            let x = inputs.get("x").and_then(|v| v.as_i64()).unwrap_or(0);
            Ok(json!({"result": x * 2}))
        });

        let func = reg.lookup("double").expect("registered");
        let mut inputs = Map::new();
        inputs.insert("x".into(), json!(5));
        let out = func(inputs).await.unwrap();
        assert_eq!(out, json!({"result": 10}));
    }

    #[tokio::test]
    async fn register_and_dispatch_async() {
        let reg = InMemoryOpRegistry::new();
        reg.register_async("echo", |inputs| async move { Ok(Value::Object(inputs)) });

        let func = reg.lookup("echo").expect("registered");
        let mut inputs = Map::new();
        inputs.insert("msg".into(), json!("hi"));
        let out = func(inputs).await.unwrap();
        assert_eq!(out, json!({"msg": "hi"}));
    }

    #[test]
    fn unknown_func_returns_none() {
        let reg = InMemoryOpRegistry::new();
        assert!(reg.lookup("missing").is_none());
    }
}
