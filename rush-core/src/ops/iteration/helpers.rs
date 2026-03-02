//! Shared helpers for iteration ops (ForOp, MapOp).
//!
//! Extracts common patterns: resolving each/broadcast params, validating lengths,
//! storing empty results, and building iteration metrics.
//! Pure Rust on serde_json::Value — no Python/GIL needed.

use serde_json::Value;

use crate::config::{IterationConfig, OpConfig};
use crate::error::RushError;
use crate::ops::base;
use crate::states::state::EngineState;

/// Resolve `each` iteration parameters to `(var_name, Value)` pairs.
/// Each value should be a `Value::Array`.
pub(crate) fn resolve_each_values(
    iter_config: &IterationConfig,
    state: &EngineState,
    context: &str,
) -> Result<Vec<(String, Value)>, RushError> {
    let mut each_values = Vec::new();
    for param in &iter_config.each {
        if let Some(value) = base::resolve_iter_param(param, state, context)? {
            each_values.push((param.var_name.clone(), value));
        }
    }
    Ok(each_values)
}

/// Resolve `broadcast` iteration parameters to `(var_name, Value)` pairs.
pub(crate) fn resolve_broadcast_values(
    iter_config: &IterationConfig,
    state: &EngineState,
    context: &str,
) -> Result<Vec<(String, Value)>, RushError> {
    let mut broadcast_values = Vec::new();
    for param in &iter_config.broadcast {
        if let Some(value) = base::resolve_iter_param(param, state, context)? {
            broadcast_values.push((param.var_name.clone(), value));
        }
    }
    Ok(broadcast_values)
}

/// Determine iteration count from `each` values and validate equal lengths.
pub(crate) fn determine_iteration_count(
    op_name: &str,
    each_values: &[(String, Value)],
    broadcast_values: &[(String, Value)],
) -> Result<usize, RushError> {
    if each_values.is_empty() {
        return Ok(if broadcast_values.is_empty() { 0 } else { 1 });
    }

    let first_len = each_values[0]
        .1
        .as_array()
        .map(|a| a.len())
        .ok_or_else(|| {
            RushError::IterationError(format!(
                "{}: each variable '{}' is not an array",
                op_name, each_values[0].0
            ))
        })?;

    for (var_name, val) in &each_values[1..] {
        let this_len = val.as_array().map(|a| a.len()).ok_or_else(|| {
            RushError::IterationError(format!(
                "{}: each variable '{}' is not an array",
                op_name, var_name
            ))
        })?;
        if this_len != first_len {
            return Err(RushError::IterationError(format!(
                "{}: each variables have different lengths: '{}' has {}, '{}' has {}",
                op_name, each_values[0].0, first_len, var_name, this_len
            )));
        }
    }

    Ok(first_len)
}

/// Store empty results for zero-iteration case.
pub(crate) fn store_empty_results(
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> Result<(), RushError> {
    for param in &op.outputs {
        if param.var_name != "iteration_metrics" {
            state.set(&op.full_name, &param.var_name, context, Value::Array(vec![]));
        }
    }

    store_iteration_metrics(op, state, context, 0, 0, 0);
    base::push_output_refs(op, state, context)?;
    Ok(())
}

/// Build and store iteration_metrics as JSON in state.
pub(crate) fn store_iteration_metrics(
    op: &OpConfig,
    state: &EngineState,
    context: &str,
    total: usize,
    success: usize,
    error: usize,
) {
    let metrics = serde_json::json!({
        "total_iterations": total,
        "success_count": success,
        "error_count": error,
    });
    state.set(&op.full_name, "iteration_metrics", context, metrics);
}

/// Build the iteration context prefix string.
pub(crate) fn context_prefix(context: &str) -> String {
    if context.is_empty() {
        String::new()
    } else {
        format!("{}.", context)
    }
}
