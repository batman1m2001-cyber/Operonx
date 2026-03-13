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

use crate::config::{OpConfig, ParamConfig, RefConfig};
use crate::logging;
use crate::error::RushError;
use crate::refs::ref_transforms::evaluate_ref_transforms;
use crate::registry::OpRegistry;
use crate::runtime;
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

/// Execute a leaf op: resolve inputs → call op → store outputs → push refs.
/// Includes error resilience: catches op errors, stores in state, continues.
/// Includes observability: enabled check, timing, logging.
pub(crate) fn run(
    op: &OpConfig,
    state: &EngineState,
    context: &str,
    registry: &Option<Arc<dyn OpRegistry>>,
) -> Result<OpResult, RushError> {
    if !op.enabled {
        return Ok(OpResult::Done);
    }

    // Start timing — Instant::now() is cheap (monotonic), always used for slow-op warnings.
    // Utc::now() is a syscall — only pay for it when tracers need timestamps.
    let perf_start = Instant::now();
    let start_time = if state.needs_timestamps { Some(Utc::now()) } else { None };

    // Try: resolve inputs → execute → store outputs
    let mut pending = false;
    let mut input_map = serde_json::Map::new();
    let mut result_obj_for_log: Option<Value> = None;
    let exec_result: Result<(), RushError> = (|| {
        for param in &op.inputs {
            if let Some(value) = resolve_param(param, state, context)? {
                input_map.insert(param.var_name.clone(), value);
            }
        }

        let result_obj = execute_op(op, input_map.clone(), state, context, registry)?;

        // Check for PENDING sentinel before storing
        if is_pending(&result_obj) {
            pending = true;
            return Ok(());
        }

        result_obj_for_log = result_obj.clone();
        store_result(op, result_obj, state, context)?;

        Ok(())
    })();

    // "finally" block — always runs
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

// =============================================================================
// Execution dispatch
// =============================================================================

/// How an op should be executed — classified once, matched once.
enum OpRoute<'a> {
    /// Non-streaming provider (LLM, embedding, rerank).
    Provider,
    /// Native transform op (prompt, parser) — synchronous.
    NativeTransform,
    /// Builtin op (direct function call from builtin_ops module).
    Builtin(&'a str),
    /// Streaming not supported in rust mode v1.
    StreamingProvider,
    /// No Rust implementation available.
    Unsupported,
}

/// Classify an op into its execution route.
fn classify_op(op: &OpConfig) -> OpRoute<'_> {
    if op.provider_config.is_some() {
        if op.stream && op.op_type == "llm" {
            return OpRoute::StreamingProvider;
        }
        return OpRoute::Provider;
    }
    if hush_providers::ops::is_native_transform_op(&op.op_type) {
        return OpRoute::NativeTransform;
    }
    if let Some(ref name) = op.rust_op {
        return OpRoute::Builtin(name);
    }
    OpRoute::Unsupported
}

/// Dispatch op execution to the appropriate handler.
/// Takes ownership of inputs to avoid redundant cloning.
fn execute_op(
    op: &OpConfig,
    inputs: serde_json::Map<String, Value>,
    _state: &EngineState,
    _context: &str,
    registry: &Option<Arc<dyn OpRegistry>>,
) -> Result<Option<Value>, RushError> {
    match classify_op(op) {
        OpRoute::StreamingProvider => Err(RushError::UnsupportedOp(
            format!(
                "Streaming (stream=true) is not supported in rust mode v1. Op: '{}'",
                op.full_name
            ),
        )),
        OpRoute::Provider => execute_provider_op(op, inputs),
        OpRoute::NativeTransform => execute_native_transform_op(&op.op_type, inputs),
        OpRoute::Builtin(spec) => execute_registry_op(spec, inputs, registry),
        OpRoute::Unsupported => {
            // Branch ops are handled separately by dispatch_op, not here.
            // If we get here, the op truly has no Rust implementation.
            Err(RushError::UnsupportedOp(format!(
                "Op '{}' (type={}) has no Rust implementation. Use @op(rust=...) to provide one.",
                op.full_name, op.op_type
            )))
        }
    }
}

// =============================================================================
// Observability helpers
// =============================================================================

/// Store timing metrics (start_time, end_time, duration_ms) in state.
fn store_timing(
    op: &OpConfig,
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
fn log_error(op: &OpConfig, state: &EngineState, context: &str, error_msg: &str) {
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
fn resolve_ref_with_parent(
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
    op: &OpConfig,
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
    op: &OpConfig,
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

// =============================================================================
// Registry op execution
// =============================================================================

/// Execute an op via the OpRegistry.
/// Accepts both "path::func" format (extracts func name) and plain "func" names.
fn execute_registry_op(
    spec: &str,
    inputs: serde_json::Map<String, Value>,
    registry: &Option<Arc<dyn OpRegistry>>,
) -> Result<Option<Value>, RushError> {
    let func_name = spec.rsplit("::").next().unwrap_or(spec);
    let input_value = Value::Object(inputs);

    let reg = registry.as_ref().ok_or_else(|| {
        RushError::RegistryError(format!(
            "No op registry loaded. Cannot dispatch rust_op='{}'. Load a plugin via --plugin or set_registry().",
            spec
        ))
    })?;

    reg.call(func_name, &input_value)
        .map(Some)
        .ok_or_else(|| {
            RushError::RegistryError(format!(
                "Unknown op function: '{}' (from rust_op='{}')",
                func_name, spec
            ))
        })
}

// =============================================================================
// Native transform op execution (synchronous)
// =============================================================================

/// Execute a native transform op (prompt, parser) via hush-providers.
fn execute_native_transform_op(
    op_type: &str,
    inputs: serde_json::Map<String, Value>,
) -> Result<Option<Value>, RushError> {
    // Move inputs into Value::Object — zero-cost, no deep clone
    let json_outputs = hush_providers::ops::execute_transform(op_type, Value::Object(inputs))
        .map_err(|e| RushError::ProviderError(format!("Native transform op error: {}", e)))?;

    Ok(Some(json_outputs))
}

// =============================================================================
// Native provider op execution (async HTTP)
// =============================================================================

/// Execute a native provider op via hush-providers (sync wrapper — DEPRECATED for new code).
fn execute_provider_op(
    op: &OpConfig,
    inputs: serde_json::Map<String, Value>,
) -> Result<Option<Value>, RushError> {
    let config = op.provider_config.as_ref().ok_or_else(|| {
        RushError::ProviderError(format!(
            "Op '{}' classified as Provider but missing provider_config",
            op.full_name
        ))
    })?;

    if !is_native_config(config) {
        return Err(RushError::UnsupportedOp(format!(
            "Op '{}' uses a non-native provider (api_type not supported in Rust mode)",
            op.full_name
        )));
    }

    // Move inputs into Value::Object — zero-cost, no deep clone
    let json_inputs = Value::Object(inputs);

    let json_outputs = runtime::block_on_async(async {
        hush_providers::ops::execute(&op.op_type, json_inputs, config).await
    })
    .map_err(|e| RushError::ProviderError(format!("Native provider op error: {}", e)))?;

    Ok(Some(json_outputs))
}

/// Check if a provider config's api_type(s) are natively supported.
fn is_native_config(config: &hush_providers::config::ProviderConfig) -> bool {
    match config {
        hush_providers::config::ProviderConfig::LLM(c) => {
            !c.configs.is_empty()
                && c.configs
                    .iter()
                    .all(|cfg| hush_providers::ops::is_native_provider_op(cfg.api_type()))
        }
        hush_providers::config::ProviderConfig::Embedding(c) => {
            hush_providers::ops::is_native_provider_op(&c.api_type)
        }
        hush_providers::config::ProviderConfig::Reranking(c) => {
            hush_providers::ops::is_native_provider_op(&c.api_type)
        }
    }
}

// =============================================================================
// Branch op execution
// =============================================================================

/// Execute a branch op: evaluate conditions, store selected target.
pub(crate) fn execute_branch(
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> Result<(), RushError> {
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

    // Resolve inputs first
    for param in &op.inputs {
        if let Some(value) = resolve_param(param, state, context)? {
            state.set(&op.full_name, &param.var_name, context, value);
        }
    }

    // Evaluate cases in order
    for case in &branch.cases {
        // Branch condition refs may use "__PARENT__" as source (from Python serialization).
        // Resolve it to the actual parent graph name.
        let cond_value = resolve_ref_with_parent(&case.condition, state, context, parent_graph)?;
        if let Some(ref val) = cond_value {
            if crate::refs::ref_transforms::is_truthy(val) {
                state.set(
                    &op.full_name,
                    "target",
                    context,
                    Value::String(case.target.clone()),
                );
                return Ok(());
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
    Ok(())
}
