//! BranchOp — conditional routing based on evaluated conditions.
//!
//! Mirrors Python's `ops/flow/branch_op.py` (BranchOp).
//! Evaluates conditions in order, stores the matched target in state.

use serde_json::{Map, Value};

use crate::config::BaseOpConfig;
use crate::error::RushError;

use crate::ops::base::{resolve_param, resolve_ref_with_parent};
use crate::ops::op_trait::{Op, OpContext};

pub struct BranchOp<'a> {
    pub config: &'a BaseOpConfig,
}

impl<'a> BranchOp<'a> {
    pub fn new(config: &'a BaseOpConfig) -> Self {
        BranchOp { config }
    }
}

impl Op for BranchOp<'_> {
    fn op_config(&self) -> &BaseOpConfig {
        self.config
    }

    fn execute_core(
        &self,
        _inputs: Map<String, Value>,
        ctx: &OpContext,
    ) -> Result<Option<Value>, RushError> {
        let op = self.config;
        let state = ctx.state;
        let context = ctx.context;

        let branch = op.branch_config.as_ref().ok_or_else(|| {
            RushError::BranchError(format!(
                "Branch op '{}' missing branch_config",
                op.full_name
            ))
        })?;

        // Derive parent graph name from op full_name (e.g. "g.router" → "g")
        let parent_graph = op
            .full_name
            .rsplit_once('.')
            .map(|(parent, _)| parent)
            .unwrap_or(&op.full_name);

        // Resolve inputs
        for param in &op.inputs {
            if let Some(value) = resolve_param(param, state, context)? {
                state.set(&op.full_name, &param.var_name, context, value);
            }
        }

        // Evaluate cases in order
        for case in &branch.cases {
            let cond_value = resolve_ref_with_parent(&case.condition, state, context, parent_graph)?;
            if let Some(ref val) = cond_value {
                if crate::refs::ref_transforms::is_truthy(val) {
                    state.set(
                        &op.full_name,
                        "target",
                        context,
                        Value::String(case.target.clone()),
                    );
                    return Ok(None);
                }
            }
        }

        // Default case
        let target = branch
            .default
            .clone()
            .unwrap_or_else(|| "__END__".to_string());
        state.set(
            &op.full_name,
            "target",
            context,
            Value::String(target),
        );
        Ok(None)
    }
}
