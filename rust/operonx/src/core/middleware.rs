//! `Middleware` — lifecycle hooks around graph execution.
//!
//! Mirrors Python [`operon/core/middleware.py`](../../../../operon/core/middleware.py).
//!
//! Ops opt into middleware by passing implementors to the engine builder.
//! Hooks fire in registered order for `before_run` and in reverse order
//! for `after_run` / `on_error` — the engine handles the chain, middleware
//! just implements the hooks it cares about.
//!
//! # Lifecycle
//! - [`before_run`] — transform inputs before the graph executes.
//! - [`after_run`]  — transform results after execution.
//! - [`on_error`]   — observe (or transform) an execution failure.
//!
//! # Thread-safety
//! Middleware is held behind `Arc` inside the engine and invoked
//! concurrently across requests — implementations must be `Send + Sync`.
//!
//! [`before_run`]: Middleware::before_run
//! [`after_run`]: Middleware::after_run
//! [`on_error`]: Middleware::on_error

use async_trait::async_trait;
use serde_json::{Map, Value};

use crate::core::exceptions::OperonError;

/// Execution context passed to every middleware hook.
///
/// The engine fills this with the per-request identifiers before invoking
/// the first `before_run` hook. Middleware may read (but not reassign) the
/// reference — scalar fields are owned strings so hooks can clone/borrow freely.
#[derive(Debug, Clone, Default)]
pub struct MiddlewareContext {
    pub user_id: String,
    pub session_id: String,
    pub request_id: String,
    /// Additional caller-supplied metadata (free-form).
    pub extra: Map<String, Value>,
}

/// Graph-execution middleware.
///
/// Mirrors Python's `Middleware` class one-for-one. All hooks are `async` —
/// we use the `async-trait` crate to make them dispatchable through
/// `dyn Middleware`.
///
/// Overriding the hook is optional — defaults pass inputs/results through
/// unchanged and re-raise errors.
#[async_trait]
pub trait Middleware: Send + Sync {
    /// Called before graph execution. Can mutate `inputs`.
    ///
    /// Returns the (possibly-modified) inputs map. The engine feeds the
    /// return value into the next middleware in the chain.
    async fn before_run(
        &self,
        inputs: Map<String, Value>,
        _ctx: &MiddlewareContext,
    ) -> Result<Map<String, Value>, OperonError> {
        Ok(inputs)
    }

    /// Called after successful graph execution. Can mutate `result`.
    async fn after_run(
        &self,
        _inputs: &Map<String, Value>,
        result: Map<String, Value>,
        _ctx: &MiddlewareContext,
    ) -> Result<Map<String, Value>, OperonError> {
        Ok(result)
    }

    /// Called when the graph failed. Default re-raises.
    ///
    /// Middleware that intends to *swallow* the error should log + return `Ok(())`.
    /// Middleware that intends to replace the error should return an `Err`
    /// carrying the new error (it propagates to any remaining middleware
    /// in reverse order, then to the caller).
    async fn on_error(
        &self,
        _inputs: &Map<String, Value>,
        error: OperonError,
        _ctx: &MiddlewareContext,
    ) -> Result<(), OperonError> {
        Err(error)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct TagInput;

    #[async_trait]
    impl Middleware for TagInput {
        async fn before_run(
            &self,
            mut inputs: Map<String, Value>,
            _ctx: &MiddlewareContext,
        ) -> Result<Map<String, Value>, OperonError> {
            inputs.insert("tagged".into(), Value::Bool(true));
            Ok(inputs)
        }
    }

    #[tokio::test]
    async fn default_hooks_passthrough() {
        struct Noop;
        #[async_trait]
        impl Middleware for Noop {}

        let m = Noop;
        let ctx = MiddlewareContext::default();

        let inputs = Map::from_iter([("a".to_string(), Value::from(1))]);
        let out = m.before_run(inputs.clone(), &ctx).await.unwrap();
        assert_eq!(out, inputs);

        let result = Map::from_iter([("b".to_string(), Value::from(2))]);
        let out2 = m.after_run(&inputs, result.clone(), &ctx).await.unwrap();
        assert_eq!(out2, result);
    }

    #[tokio::test]
    async fn before_run_can_mutate_inputs() {
        let m = TagInput;
        let ctx = MiddlewareContext::default();
        let out = m.before_run(Map::new(), &ctx).await.unwrap();
        assert_eq!(out.get("tagged"), Some(&Value::Bool(true)));
    }
}
