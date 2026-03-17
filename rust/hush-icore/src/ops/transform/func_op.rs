//! FuncOp — execute a custom Rust function as a workflow op.
//!
//! Mirrors Python's `ops/transform/func_op.py` (FuncOp).
//! Dispatches to the OpRegistry by func_name.

use serde_json::{Map, Value};

use crate::config::BaseOpConfig;
use crate::error::RushError;

use crate::ops::op_trait::{Op, OpContext};

/// FuncOp — custom Rust function registered via `#[hush_op]`.
///
/// Mirrors Python's FuncOp class. The `execute_core()` method
/// calls `OpRegistry::call(func_name, inputs)`.
pub struct FuncOp<'a> {
    pub config: &'a BaseOpConfig,
    pub func_name: &'a str,
}

impl<'a> FuncOp<'a> {
    pub fn new(config: &'a BaseOpConfig) -> Self {
        let func_name = config.func_name.as_deref().unwrap_or(&config.name);
        FuncOp { config, func_name }
    }
}

impl Op for FuncOp<'_> {
    fn op_config(&self) -> &BaseOpConfig {
        self.config
    }

    fn execute_core(
        &self,
        inputs: Map<String, Value>,
        ctx: &OpContext,
    ) -> Result<Option<Value>, RushError> {
        let input_value = Value::Object(inputs);

        let reg = ctx.registry.as_ref().ok_or_else(|| {
            RushError::RegistryError(format!(
                "No op registry loaded. Cannot dispatch func '{}'.",
                self.func_name
            ))
        })?;

        reg.call(self.func_name, &input_value)
            .map(Some)
            .ok_or_else(|| {
                let available = reg.available_ops();
                RushError::RegistryError(format!(
                    "Unknown op function: '{}'. Available: {:?}",
                    self.func_name, available
                ))
            })
    }
}
