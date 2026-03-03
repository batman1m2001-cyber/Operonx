//! MapOp execution — parallel iteration with concurrency control.
//!
//! Mirrors Python's `ops/iteration/map_op.py` (MapOp).
//! Unlike ForOp (sequential), MapOp runs iterations concurrently using rayon.
//! Pure Rust on serde_json::Value — no Python/GIL needed.
//! True multi-core parallelism (no GIL contention).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use rayon::prelude::*;
use serde_json::Value;

use crate::config::OpConfig;
use crate::error::RushError;
use crate::ops::base;
use crate::ops::graph::graph_op;
use crate::ops::iteration::helpers;
use crate::states::state::EngineState;

/// Execute a MapOp: resolve each/broadcast → iterate concurrently → transpose results.
pub(crate) fn run(
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> Result<(), RushError> {
    let inner = op.inner_graph.as_ref().ok_or_else(|| {
        RushError::IterationError(format!(
            "MapOp '{}' missing inner_graph config",
            op.full_name
        ))
    })?;
    let iter_config = op.iteration_config.as_ref().ok_or_else(|| {
        RushError::IterationError(format!(
            "MapOp '{}' missing iteration_config",
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
                    "MapOp '{}': each variable '{}' is not an array",
                    op.full_name, var_name
                ))
            })?;
            items.push((var_name.clone(), arr[i].clone()));
        }
        per_iter_each.push(items);
    }

    // 4. Set up concurrent execution
    let max_concurrency = iter_config
        .max_concurrency
        .unwrap_or_else(|| std::thread::available_parallelism().map(|p| p.get()).unwrap_or(4));
    let fail_fast = iter_config.fail_fast;
    let ctx_prefix = helpers::context_prefix(context);

    // Result storage (thread-safe)
    let results: Vec<Mutex<Option<Result<Value, String>>>> =
        (0..n).map(|_| Mutex::new(None)).collect();
    let should_stop = AtomicBool::new(false);

    // 5. Process in chunks of max_concurrency using rayon — NO GIL NEEDED
    for chunk_start in (0..n).step_by(max_concurrency) {
        if should_stop.load(Ordering::Relaxed) {
            break;
        }

        let chunk_end = (chunk_start + max_concurrency).min(n);
        let indices: Vec<usize> = (chunk_start..chunk_end).collect();

        indices.par_iter().for_each(|&i| {
            if should_stop.load(Ordering::Relaxed) {
                return;
            }

            let iter_context = format!("{}[{}]", ctx_prefix, i);

            // Store each items for this iteration
            for (var_name, item) in &per_iter_each[i] {
                state.set(&op.full_name, var_name, &iter_context, item.clone());
            }

            // Store broadcast values
            for (var_name, val) in &broadcast_values {
                state.set(&op.full_name, var_name, &iter_context, val.clone());
            }

            // Run inner graph + collect outputs
            let iter_result = graph_op::run_graph(inner, state, &iter_context)
                .and_then(|_| graph_op::get_outputs(inner, state, &iter_context));

            match iter_result {
                Ok(output) => {
                    *results[i].lock().unwrap() = Some(Ok(output));
                }
                Err(err) => {
                    let err_msg = format!("{}", err);
                    if fail_fast {
                        should_stop.store(true, Ordering::Relaxed);
                    } else {
                        log::warn!(
                            "[rush] MapOp '{}' iteration {} failed: {}",
                            op.full_name,
                            i,
                            err_msg
                        );
                    }
                    *results[i].lock().unwrap() = Some(Err(err_msg));
                }
            }
        });
    }

    // 6. Check fail_fast — propagate first error
    if fail_fast && should_stop.load(Ordering::Relaxed) {
        for i in 0..n {
            if let Some(Err(ref msg)) = *results[i].lock().unwrap() {
                return Err(RushError::IterationError(format!(
                    "MapOp '{}' iteration {} failed: {}",
                    op.full_name, i, msg
                )));
            }
        }
    }

    // 7. Build result list
    let mut result_objects: Vec<Value> = Vec::with_capacity(n);
    let mut success_count: usize = 0;

    for i in 0..n {
        match results[i].lock().unwrap().take() {
            Some(Ok(output)) => {
                result_objects.push(output);
                success_count += 1;
            }
            Some(Err(err_msg)) => {
                result_objects.push(serde_json::json!({
                    "error": err_msg,
                    "error_type": "RushError",
                }));
            }
            None => {
                result_objects.push(serde_json::json!({
                    "error": "Skipped due to fail_fast",
                    "error_type": "Skipped",
                }));
            }
        }
    }

    // 8. Transpose results and store
    transpose_and_store(op, &result_objects, state, context)?;

    // 9. Add iteration_metrics and push output refs
    helpers::store_iteration_metrics(op, state, context, n, success_count, n - success_count);
    base::push_output_refs(op, state, context)?;

    Ok(())
}

// =============================================================================
// Helpers (shared with for_op)
// =============================================================================

/// Transpose result dicts and store each key as a list in state.
///
/// When the op has explicit outputs, transpose only those keys.
/// When outputs is empty, auto-detect keys from the iteration results
/// (matching Python's behavior where ForOp/MapOp auto-collects all output keys).
pub(super) fn transpose_and_store(
    op: &OpConfig,
    results: &[Value],
    state: &EngineState,
    context: &str,
) -> Result<(), RushError> {
    let explicit_keys: Vec<&str> = op
        .outputs
        .iter()
        .filter(|p| p.var_name != "iteration_metrics")
        .map(|p| p.var_name.as_str())
        .collect();

    if explicit_keys.is_empty() {
        // Auto-detect keys from all result objects
        let mut seen = std::collections::BTreeSet::new();
        for r in results {
            if let Value::Object(map) = r {
                for k in map.keys() {
                    seen.insert(k.clone());
                }
            }
        }
        for key in &seen {
            let list: Vec<Value> = results
                .iter()
                .map(|r| {
                    if let Value::Object(map) = r {
                        map.get(key.as_str()).cloned().unwrap_or(Value::Null)
                    } else {
                        Value::Null
                    }
                })
                .collect();
            state.set(&op.full_name, key, context, Value::Array(list));
        }
    } else {
        for key in &explicit_keys {
            let list: Vec<Value> = results
                .iter()
                .map(|r| {
                    if let Value::Object(map) = r {
                        map.get(*key).cloned().unwrap_or(Value::Null)
                    } else {
                        Value::Null
                    }
                })
                .collect();
            state.set(&op.full_name, key, context, Value::Array(list));
        }
    }

    Ok(())
}
