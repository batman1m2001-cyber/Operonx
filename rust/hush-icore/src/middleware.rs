//! Middleware system for the Hush engine.
//!
//! Middleware provides hooks into the engine execution lifecycle:
//! - `before_run`: modify inputs before execution
//! - `after_run`: modify results after execution
//! - `on_error`: handle errors during execution

use serde_json::Value;

use crate::error::RushError;

/// Trait for engine middleware.
///
/// Implement any subset of hooks to add behavior.
/// `before_run` and `after_run` run in registration order and reverse order respectively.
pub trait Middleware: Send + Sync {
    /// Called before graph execution. Can modify inputs in-place.
    fn before_run(&self, _inputs: &mut Value) -> Result<(), RushError> {
        Ok(())
    }

    /// Called after graph execution. Can modify result in-place.
    fn after_run(&self, _result: &mut Value) -> Result<(), RushError> {
        Ok(())
    }

    /// Called when graph execution fails.
    fn on_error(&self, _error: &RushError) {}
}
