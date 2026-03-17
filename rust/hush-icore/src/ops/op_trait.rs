//! Op trait — mirrors Python's BaseOp.
//!
//! Every op type (FuncOp, GraphOp, BranchOp, LlmOp, etc.) implements this trait.
//! The `run()` method has a default implementation that handles the shared flow:
//! get_inputs → cache check → execute_core → cache store → store_result → push refs.
//!
//! Each op only implements `execute_core()` — like Python's `BaseOp._exec_core()`.

use std::sync::{Arc, OnceLock};

use serde_json::{Map, Value};

use crate::config::BaseOpConfig;
use crate::error::RushError;
use crate::registry::OpRegistry;
use crate::states::state::EngineState;

use super::base::OpResult;

// Global op factory — set by hush-serve at startup, used by base.rs for provider ops.
static GLOBAL_FACTORY: OnceLock<Arc<dyn OpFactory>> = OnceLock::new();

/// Set the global op factory. Called once by hush-serve at startup.
pub fn set_global_factory(factory: Arc<dyn OpFactory>) {
    let _ = GLOBAL_FACTORY.set(factory);
}

/// Get the global op factory.
pub fn get_global_factory() -> Option<Arc<dyn OpFactory>> {
    GLOBAL_FACTORY.get().cloned()
}

/// Execution context passed to every op — borrows from the engine.
pub struct OpContext<'a> {
    pub state: &'a EngineState,
    pub context: &'a str,
    pub registry: &'a Option<Arc<dyn OpRegistry>>,
}

/// Core trait for all ops — mirrors Python's BaseOp.
///
/// Shared behavior (resolve inputs, store results, caching, timing, logging)
/// lives in the default `run()` implementation. Each op only overrides
/// `execute_core()` — the equivalent of Python's `_exec_core()`.
pub trait Op: Send + Sync {
    /// Access the underlying BaseOpConfig (every op has one).
    fn op_config(&self) -> &BaseOpConfig;

    /// Execute the op's core logic — like Python's `BaseOp._exec_core()`.
    ///
    /// Receives resolved inputs as a JSON map. Returns output JSON object.
    /// This is the ONLY method each op must implement.
    fn execute_core(
        &self,
        inputs: Map<String, Value>,
        ctx: &OpContext,
    ) -> Result<Option<Value>, RushError>;

    /// Main entry point — like Python's `BaseOp.run()`.
    ///
    /// Default implementation handles the full lifecycle:
    /// enabled check → timing → resolve inputs → cache check →
    /// execute_core → cache store → store result → push refs → logging.
    ///
    /// Ops should NOT override this unless they need fundamentally different
    /// execution flow (e.g., GraphOp runs the scheduler).
    fn run(&self, ctx: &OpContext) -> Result<OpResult, RushError> {
        super::base::run_with_core(self.op_config(), ctx, |inputs| {
            self.execute_core(inputs, ctx)
        })
    }
}

/// Factory for constructing provider ops from config.
///
/// hush-serve registers a factory that knows about LlmOp, EmbeddingOp, etc.
/// hush-icore's scheduler calls the factory for op types it doesn't know about
/// (anything that's not code, branch, graph, parser).
pub trait OpFactory: Send + Sync {
    /// Create an Op from config. Returns None if the op type is not recognized.
    fn create_op<'a>(&self, config: &'a BaseOpConfig) -> Option<Box<dyn Op + 'a>>;
}

