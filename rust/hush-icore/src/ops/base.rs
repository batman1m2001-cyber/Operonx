//! Base op execution — leaf ops, ref resolution, result storage.
//!
//! Mirrors Python's `ops/base.py` (BaseOp.run, store_result, resolve).
//! Pure Rust on serde_json::Value — no Python/GIL needed.
//! Includes observability: enabled flag, per-op timing, $tags, verbose logging,
//! and slow op warnings.

use std::sync::Arc;
use std::time::Instant;

use chrono::Utc;
use serde_json::Value;

use crate::config::{BaseOpConfig, ParamConfig, RefConfig};
use crate::logging;
use crate::error::RushError;
use crate::refs::ref_transforms::evaluate_ref_transforms;
use crate::states::state::EngineState;

// =============================================================================
// Leaf op execution (BaseOp.run equivalent)
// =============================================================================

/// Op execution result — indicates whether downstream propagation should happen.
#[derive(Debug, PartialEq)]
pub(crate) enum OpResult {
    /// Op completed normally — propagate to successors.
    Done,
    /// Op returned PENDING — no propagation.
    Pending,
}

/// Check if a result contains the PENDING sentinel.
fn is_pending(result: &Option<Value>) -> bool {
    match result {
        Some(Value::Object(map)) => map
            .get("__pending__")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        _ => false,
    }
}

/// Execute a leaf op with a custom execute function (closure).
/// This is the ONLY entry point for op execution — called by Op::run() default impl.
/// Used by `Op::run()` default impl — the closure calls `self.execute_core()`.
pub(crate) fn run_with_core<F>(
    op: &BaseOpConfig,
    ctx: &super::op_trait::OpContext,
    execute_fn: F,
) -> Result<OpResult, RushError>
where
    F: FnOnce(serde_json::Map<String, Value>) -> Result<Option<Value>, RushError>,
{
    let state = ctx.state;
    let context = ctx.context;

    if !op.enabled {
        return Ok(OpResult::Done);
    }

    let perf_start = Instant::now();
    let start_time = if state.needs_timestamps { Some(Utc::now()) } else { None };

    let mut pending = false;
    let mut input_map = serde_json::Map::new();
    let mut result_obj_for_log: Option<Value> = None;
    let exec_result: Result<(), RushError> = (|| {
        for param in &op.inputs {
            if let Some(value) = resolve_param(param, state, context)? {
                input_map.insert(param.var_name.clone(), value);
            }
        }

        // Cache check
        let op_cache = if op.cache.is_some() {
            super::cache::get_op_cache(&op.full_name)
        } else {
            None
        };
        let input_hash = if op_cache.is_some() {
            Some(super::cache::hash_inputs(&input_map))
        } else {
            None
        };

        if let (Some(ref cache), Some(hash)) = (&op_cache, input_hash) {
            if let Some(cached) = cache.get(hash) {
                result_obj_for_log = Some(cached.clone());
                store_result(op, Some(cached), state, context)?;
                return Ok(());
            }
        }

        // Execute via closure (Op::execute_core)
        let result_obj = execute_fn(input_map.clone())?;

        if is_pending(&result_obj) {
            pending = true;
            return Ok(());
        }

        if let (Some(ref cache), Some(hash), Some(ref result)) = (&op_cache, input_hash, &result_obj) {
            cache.insert(hash, result.clone());
        }

        result_obj_for_log = result_obj.clone();
        store_result(op, result_obj, state, context)?;

        Ok(())
    })();

    // Timing + logging (same as run())
    let duration_ms = if let Some(st) = start_time {
        store_timing(op, state, context, st, perf_start)
    } else {
        perf_start.elapsed().as_secs_f64() * 1000.0
    };

    if let Err(ref err) = exec_result {
        log_error(op, state, context, &format!("{}", err));
    }

    if duration_ms > 100.0 {
        let req_id = state.request_id().unwrap_or_else(|| "unknown".to_string());
        log::warn!("{}", logging::format_event("op_slow", &[
            ("request_id", &req_id),
            ("full_name", &op.full_name),
            ("duration_ms", &format!("{:.1}", duration_ms)),
        ]));
    }

    if op.verbose {
        let req_id = state.request_id().unwrap_or_else(|| "unknown".to_string());
        let input_summary = logging::format_data(&Value::Object(input_map), 80, 8);
        let output_summary = match &result_obj_for_log {
            Some(v) => logging::format_data(v, 80, 8),
            None => "{}".to_string(),
        };
        log::info!("{}", logging::format_event("op_done", &[
            ("request_id", &req_id),
            ("op_type", &op.op_type.to_uppercase()),
            ("full_name", &op.full_name),
            ("context", context),
            ("duration_ms", &format!("{:.1}", duration_ms)),
            ("inputs", &input_summary),
            ("outputs", &output_summary),
        ]));
    }

    if exec_result.is_ok() && !pending {
        push_output_refs(op, state, context)?;
    }

    Ok(if pending { OpResult::Pending } else { OpResult::Done })
}

// Execution dispatch removed — now handled by Op trait structs via dispatch_leaf_op() in graph_op.rs.

// =============================================================================
// Observability helpers
// =============================================================================

/// Store timing metrics (start_time, end_time, duration_ms) in state.
fn store_timing(
    op: &BaseOpConfig,
    state: &EngineState,
    context: &str,
    start_time: chrono::DateTime<Utc>,
    perf_start: Instant,
) -> f64 {
    let duration_ms = perf_start.elapsed().as_secs_f64() * 1000.0;
    let end_time = Utc::now();

    state.set(
        &op.full_name,
        "$start_time",
        context,
        Value::String(start_time.to_rfc3339()),
    );
    state.set(
        &op.full_name,
        "$end_time",
        context,
        Value::String(end_time.to_rfc3339()),
    );
    state.set(
        &op.full_name,
        "$duration_ms",
        context,
        serde_json::json!(duration_ms),
    );

    duration_ms
}

/// Log an error and store it in state.
fn log_error(op: &BaseOpConfig, state: &EngineState, context: &str, error_msg: &str) {
    state.set(
        &op.full_name,
        "error",
        context,
        Value::String(error_msg.to_string()),
    );
    let req_id = state.request_id().unwrap_or_else(|| "unknown".to_string());
    log::error!("{}", logging::format_event("op_error", &[
        ("request_id", &req_id),
        ("name", &op.full_name),
        ("error", error_msg),
    ]));
}

// =============================================================================
// Ref resolution
// =============================================================================

/// Resolve a parameter to its value by checking ref, literal, default.
pub(crate) fn resolve_param(
    param: &ParamConfig,
    state: &EngineState,
    context: &str,
) -> Result<Option<Value>, RushError> {
    if let Some(ref ref_config) = param.ref_config {
        if let Some(value) = resolve_ref(ref_config, state, context)? {
            return Ok(Some(value));
        }
    }

    if let Some(ref literal) = param.literal {
        return Ok(Some(literal.clone()));
    }

    if let Some(ref default) = param.default_value {
        return Ok(Some(default.clone()));
    }

    Ok(None)
}

/// Resolve a Ref config to its value from state.
pub(crate) fn resolve_ref(
    ref_config: &RefConfig,
    state: &EngineState,
    context: &str,
) -> Result<Option<Value>, RushError> {
    let value = state.get(&ref_config.source, &ref_config.var, context);

    match value {
        Some(arc_val) => {
            let val = Arc::try_unwrap(arc_val).unwrap_or_else(|arc| (*arc).clone());
            if ref_config.transforms.is_empty() {
                Ok(Some(val))
            } else {
                let result = evaluate_ref_transforms(val, &ref_config.transforms, state, context)?;
                Ok(Some(result))
            }
        }
        None => Ok(None),
    }
}

/// Resolve a Ref config, replacing "__PARENT__" source with the actual parent graph name.
///
/// Branch condition refs serialize `PARENT` as `"__PARENT__"` (literal string sentinel),
/// but regular op inputs resolve to the actual graph name during Python build().
/// This function handles that translation so branch conditions find the right state values.
pub(crate) fn resolve_ref_with_parent(
    ref_config: &RefConfig,
    state: &EngineState,
    context: &str,
    parent_graph: &str,
) -> Result<Option<Value>, RushError> {
    let source = if ref_config.source == "__PARENT__" {
        parent_graph
    } else {
        &ref_config.source
    };

    let value = state.get(source, &ref_config.var, context);

    match value {
        Some(arc_val) => {
            let val = Arc::try_unwrap(arc_val).unwrap_or_else(|arc| (*arc).clone());
            if ref_config.transforms.is_empty() {
                Ok(Some(val))
            } else {
                let result = evaluate_ref_transforms(val, &ref_config.transforms, state, context)?;
                Ok(Some(result))
            }
        }
        None => Ok(None),
    }
}

// =============================================================================
// Result storage and output forwarding
// =============================================================================

/// Store an op's execution result into state.
pub(crate) fn store_result(
    op: &BaseOpConfig,
    result_obj: Option<Value>,
    state: &EngineState,
    context: &str,
) -> Result<(), RushError> {
    if let Some(Value::Object(map)) = result_obj {
        for (key, value) in map {
            // Handle $tags specially
            if key == "$tags" {
                if let Value::Array(arr) = &value {
                    let tags: Vec<String> = arr
                        .iter()
                        .filter_map(|v| v.as_str().map(|s| s.to_string()))
                        .collect();
                    state.add_tags(tags);
                }
                continue;
            }
            // Skip internal $-prefixed keys
            if key.starts_with('$') {
                continue;
            }
            state.set(&op.full_name, &key, context, value);
        }
    }
    Ok(())
}

/// Push output refs — forward op outputs to parent/destination state.
pub(crate) fn push_output_refs(
    op: &BaseOpConfig,
    state: &EngineState,
    context: &str,
) -> Result<(), RushError> {
    for param in &op.outputs {
        if let Some(ref ref_config) = param.ref_config {
            if let Some(arc_val) = state.get(&op.full_name, &param.var_name, context) {
                let value = Arc::try_unwrap(arc_val).unwrap_or_else(|arc| (*arc).clone());
                state.set(&ref_config.source, &ref_config.var, context, value);
            }
        }
    }
    Ok(())
}

// Old dispatch functions (execute_registry_op, execute_provider_op, execute_branch)
// removed — now handled by Op trait structs:
//   FuncOp (transform/func_op.rs)
//   BranchOp (flow/branch_op.rs)
//   LlmOp, EmbeddingOp, etc. (hush-providers/src/ops/)
