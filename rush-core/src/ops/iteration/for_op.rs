//! ForOp execution — iterate over lists with broadcast support.
//!
//! Mirrors Python's `ops/iteration/for_op.py` (ForOp).
//! Pure Rust on serde_json::Value — no Python/GIL needed.

use serde_json::Value;

use crate::config::OpConfig;
use crate::error::RushError;
use crate::ops::base;
use crate::ops::graph::graph_op;
use crate::ops::iteration::helpers;
use crate::states::state::EngineState;

/// Execute a ForOp: resolve each/broadcast → iterate → transpose results.
pub(crate) fn run(
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> Result<(), RushError> {
    let inner = op.inner_graph.as_ref().ok_or_else(|| {
        RushError::IterationError(format!(
            "ForOp '{}' missing inner_graph config",
            op.full_name
        ))
    })?;
    let iter_config = op.iteration_config.as_ref().ok_or_else(|| {
        RushError::IterationError(format!(
            "ForOp '{}' missing iteration_config",
            op.full_name
        ))
    })?;

    // 1. Resolve op inputs from parent context
    for param in &op.inputs {
        if let Some(value) = base::resolve_param(param, state, context)? {
            state.set(&op.full_name, &param.var_name, context, value);
        }
    }

    // 2. Resolve each/broadcast values and determine iteration count
    let each_values = helpers::resolve_each_values(iter_config, state, context)?;
    let broadcast_values = helpers::resolve_broadcast_values(iter_config, state, context)?;
    let n = helpers::determine_iteration_count(&op.full_name, &each_values, &broadcast_values)?;

    if n == 0 {
        helpers::store_empty_results(op, state, context)?;
        return Ok(());
    }

    // 3. Pre-extract each[var][i] items
    let mut per_iter_each: Vec<Vec<(String, Value)>> = Vec::with_capacity(n);
    for i in 0..n {
        let mut items = Vec::with_capacity(each_values.len());
        for (var_name, list_val) in &each_values {
            let arr = list_val.as_array().ok_or_else(|| {
                RushError::IterationError(format!(
                    "ForOp '{}': each variable '{}' is not an array",
                    op.full_name, var_name
                ))
            })?;
            items.push((var_name.clone(), arr[i].clone()));
        }
        per_iter_each.push(items);
    }

    // 4. Iterate
    let ctx_prefix = helpers::context_prefix(context);
    let mut results: Vec<Value> = Vec::with_capacity(n);
    let mut success_count: usize = 0;

    for i in 0..n {
        let iter_context = format!("{}[{}]", ctx_prefix, i);

        // Store each[var][i] into (op.full_name, var, iter_context)
        for (var_name, item) in &per_iter_each[i] {
            state.set(&op.full_name, var_name, &iter_context, item.clone());
        }

        // Store broadcast values
        for (var_name, val) in &broadcast_values {
            state.set(&op.full_name, var_name, &iter_context, val.clone());
        }

        // Run inner graph — catch errors
        let iter_result = graph_op::run_graph(inner, state, &iter_context)
            .and_then(|_| graph_op::get_outputs(inner, state, &iter_context));

        match iter_result {
            Ok(output) => {
                results.push(output);
                success_count += 1;
            }
            Err(err) => {
                if iter_config.fail_fast {
                    return Err(err);
                }
                log::warn!(
                    "[rush] ForOp '{}' iteration {} failed: {}",
                    op.full_name,
                    i,
                    err
                );
                let error_obj = serde_json::json!({
                    "error": format!("{}", err),
                    "error_type": "RushError",
                });
                results.push(error_obj);
            }
        }
    }

    // 5. Transpose results and store
    super::map_op::transpose_and_store(op, &results, state, context)?;

    // 6. Add iteration_metrics and push output refs
    helpers::store_iteration_metrics(op, state, context, n, success_count, n - success_count);
    base::push_output_refs(op, state, context)?;

    Ok(())
}
