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

use crate::config::{IterParamConfig, OpConfig, ParamConfig, RefConfig};
use crate::error::RushError;
use crate::plugins;
use crate::refs::interpreter::evaluate_ref_ops;
use crate::runtime;
use crate::states::state::EngineState;

// =============================================================================
// Leaf op execution (BaseOp.run equivalent)
// =============================================================================

/// Execute a leaf op: resolve inputs → call op → store outputs → push refs.
/// Includes error resilience: catches op errors, stores in state, continues.
/// Includes observability: enabled check, timing, logging.
pub(crate) fn run(
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> Result<(), RushError> {
    if !op.enabled {
        return Ok(());
    }

    // Start timing
    let start_time = Utc::now();
    let perf_start = Instant::now();

    // Try: resolve inputs → execute → store outputs
    let exec_result: Result<(), RushError> = (|| {
        let mut inputs = serde_json::Map::new();
        for param in &op.inputs {
            if let Some(value) = resolve_param(param, state, context)? {
                inputs.insert(param.var_name.clone(), value);
            }
        }

        let result_obj = execute_op(op, &inputs, state, context)?;
        store_result(op, result_obj, state, context)?;

        Ok(())
    })();

    // "finally" block — always runs
    let duration_ms = store_timing(op, state, context, start_time, perf_start);

    if let Err(ref err) = exec_result {
        log_error(op, state, context, &format!("{}", err));
    }

    if duration_ms > 100.0 {
        log::warn!("[rush] Slow op {}: {:.1}ms", op.full_name, duration_ms);
    }

    if op.verbose {
        log::info!(
            "[rush] {}: {} ({:.1}ms)",
            op.op_type.to_uppercase(),
            op.full_name,
            duration_ms
        );
    }

    if exec_result.is_ok() {
        push_output_refs(op, state, context)?;
    }

    Ok(())
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
    /// Plugin op from a cdylib shared library ("path/to/lib.so::func").
    Plugin(&'a str),
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
    if rush_providers::ops::is_native_transform_op(&op.op_type) {
        return OpRoute::NativeTransform;
    }
    if let Some(ref name) = op.rust_op {
        if name.contains("::") {
            return OpRoute::Plugin(name);
        }
    }
    OpRoute::Unsupported
}

/// Dispatch op execution to the appropriate handler.
fn execute_op(
    op: &OpConfig,
    inputs: &serde_json::Map<String, Value>,
    _state: &EngineState,
    _context: &str,
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
        OpRoute::Plugin(spec) => execute_plugin_op(spec, inputs),
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
        "start_time",
        context,
        Value::String(start_time.to_rfc3339()),
    );
    state.set(
        &op.full_name,
        "end_time",
        context,
        Value::String(end_time.to_rfc3339()),
    );
    state.set(
        &op.full_name,
        "duration_ms",
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
    log::error!("[rush] Error in op {}: {}", op.full_name, error_msg);
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
            if ref_config.ops.is_empty() {
                Ok(Some(val))
            } else {
                let result = evaluate_ref_ops(val, &ref_config.ops, state, context)?;
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
            if ref_config.ops.is_empty() {
                Ok(Some(val))
            } else {
                let result = evaluate_ref_ops(val, &ref_config.ops, state, context)?;
                Ok(Some(result))
            }
        }
        None => Ok(None),
    }
}

/// Resolve an iteration parameter (each or broadcast) to its value.
pub(crate) fn resolve_iter_param(
    param: &IterParamConfig,
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

    Ok(None)
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
// Plugin op execution
// =============================================================================

/// Execute a plugin op from a cdylib shared library.
fn execute_plugin_op(
    spec: &str,
    inputs: &serde_json::Map<String, Value>,
) -> Result<Option<Value>, RushError> {
    let (lib_path, func_name) = plugins::parse_plugin_spec(spec).ok_or_else(|| {
        RushError::PluginError(format!(
            "Invalid plugin spec '{}': expected 'path/to/lib.so::func_name'",
            spec
        ))
    })?;

    let json_value = Value::Object(inputs.clone());
    let json_bytes = serde_json::to_vec(&json_value).map_err(|e| {
        RushError::PluginError(format!("Failed to serialize inputs for plugin op: {}", e))
    })?;

    let result_value = plugins::load_and_call(lib_path, func_name, &json_bytes)
        .map_err(|e| RushError::PluginError(format!("Plugin op error: {}", e)))?;

    Ok(Some(result_value))
}

// =============================================================================
// Native transform op execution (synchronous)
// =============================================================================

/// Execute a native transform op (prompt, parser) via rush-providers.
fn execute_native_transform_op(
    op_type: &str,
    inputs: &serde_json::Map<String, Value>,
) -> Result<Option<Value>, RushError> {
    let json_inputs = Value::Object(inputs.clone());

    let json_outputs = rush_providers::ops::execute_transform(op_type, json_inputs)
        .map_err(|e| RushError::ProviderError(format!("Native transform op error: {}", e)))?;

    Ok(Some(json_outputs))
}

// =============================================================================
// Native provider op execution (async HTTP)
// =============================================================================

/// Execute a native provider op via rush-providers.
fn execute_provider_op(
    op: &OpConfig,
    inputs: &serde_json::Map<String, Value>,
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

    let json_inputs = Value::Object(inputs.clone());

    let json_outputs = runtime::block_on_async(async {
        rush_providers::ops::execute(&op.op_type, json_inputs, config).await
    })
    .map_err(|e| RushError::ProviderError(format!("Native provider op error: {}", e)))?;

    Ok(Some(json_outputs))
}

/// Check if a provider config's api_type(s) are natively supported.
fn is_native_config(config: &rush_providers::config::ProviderConfig) -> bool {
    match config {
        rush_providers::config::ProviderConfig::LLM(c) => {
            !c.configs.is_empty()
                && c.configs
                    .iter()
                    .all(|cfg| rush_providers::ops::is_native_provider_op(cfg.api_type()))
        }
        rush_providers::config::ProviderConfig::Embedding(c) => {
            rush_providers::ops::is_native_provider_op(&c.api_type)
        }
        rush_providers::config::ProviderConfig::Reranking(c) => {
            rush_providers::ops::is_native_provider_op(&c.api_type)
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
            if crate::refs::interpreter::is_truthy(val) {
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
