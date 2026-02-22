//! Base op execution — leaf ops, ref resolution, result storage.
//!
//! Mirrors Python's `ops/base.py` (BaseOp.run, store_result, resolve).
//! Includes observability: enabled flag, per-op timing, $tags, verbose logging,
//! slow op warnings, and execution order tracking.

use std::sync::mpsc;
use std::time::{Duration, Instant};

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::config::{IterParamConfig, OpConfig, ParamConfig, RefConfig};
use crate::ops::transform::func_op;
use crate::refs::interpreter::evaluate_ref_ops;
use crate::runtime;
use crate::states::state::EngineState;

// =============================================================================
// Leaf op execution (BaseOp.run equivalent)
// =============================================================================

/// Execute a leaf op: resolve inputs → call op → store outputs → push refs.
/// Includes error resilience: catches op errors, stores in state, continues.
/// Includes observability: execution order, enabled check, timing, logging.
/// Mirrors Python's BaseOp.run() try/except/finally pattern.
pub(crate) fn execute_leaf_op(
    py: Python,
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    // 0. Record execution order (before enabled check — mirrors Python)
    let parent = op
        .full_name
        .rsplit_once('.')
        .map(|(p, _)| p.to_string())
        .unwrap_or_default();
    state.record_execution(op.full_name.clone(), parent, context.to_string());

    // 1. Check enabled flag — skip if disabled (mirrors base.py:734-737)
    if !op.enabled {
        return Ok(());
    }

    // 2. Start timing (mirrors base.py:740-741)
    let perf_start = Instant::now();

    // 3-5. Try: resolve inputs → execute → store outputs (mirrors base.py:746-755)
    let exec_result: PyResult<()> = (|| {
        // 3. Resolve inputs
        let inputs_dict = PyDict::new_bound(py);
        for param in &op.inputs {
            if let Some(value) = resolve_param(py, param, state, context)? {
                inputs_dict.set_item(&param.var_name, value.bind(py))?;
            }
        }

        // 4. Execute: try native provider op (with streaming), then Rust registry, then Python callable
        let result_obj = if op.provider_config.is_some() && op.stream && op.op_type == "llm" {
            execute_provider_op_streaming(py, op, &inputs_dict, state, context)?
        } else if op.provider_config.is_some() {
            execute_provider_op(py, op, &inputs_dict)?
        } else if let Some(ref rust_name) = op.rust_op {
            if func_op::has_internal(rust_name) {
                func_op::execute_internal(py, rust_name, &inputs_dict.as_borrowed())?
            } else {
                call_python(py, op, &inputs_dict)?
            }
        } else {
            call_python(py, op, &inputs_dict)?
        };

        // 5. Store outputs (store_result handles $tags extraction)
        store_result(py, op, result_obj, state, context)?;

        Ok(())
    })();

    // === "finally" block — always runs (mirrors base.py:767-782) ===

    // 6. End timing + store duration_ms
    let duration_ms = perf_start.elapsed().as_secs_f64() * 1000.0;
    state.set(
        op.full_name.clone(),
        "duration_ms".to_string(),
        context.to_string(),
        duration_ms.to_object(py),
    );

    // Handle error from execution (mirrors base.py:757-765)
    if let Err(ref err) = exec_result {
        let error_msg = format!("{}", err);
        state.set(
            op.full_name.clone(),
            "error".to_string(),
            context.to_string(),
            error_msg.to_object(py),
        );

        let logging = py.import_bound("logging")?;
        let logger = logging.call_method1("getLogger", ("hush.core",))?;
        logger.call_method1(
            "error",
            (format!(
                "[rush] Error in op {}: {}",
                op.full_name, error_msg
            ),),
        )?;
    }

    // 7. Slow op warning >100ms (mirrors base.py:775-782)
    if duration_ms > 100.0 {
        let warnings = py.import_bound("warnings")?;
        warnings.call_method1(
            "warn",
            (format!(
                "Slow op {}: {:.1}ms",
                op.full_name, duration_ms
            ),),
        )?;
    }

    // 8. Verbose logging (mirrors base.py:696-716)
    if op.verbose {
        let logging = py.import_bound("logging")?;
        let logger = logging.call_method1("getLogger", ("hush.core",))?;
        logger.call_method1(
            "info",
            (format!(
                "[rush] {}: {} ({:.1}ms)",
                op.op_type.to_uppercase(),
                op.full_name,
                duration_ms
            ),),
        )?;
    }

    // 9. Push output refs (only if execution succeeded)
    if exec_result.is_ok() {
        push_output_refs(py, op, state, context)?;
    }

    // Always return Ok — error is stored in state, graph continues
    Ok(())
}

// =============================================================================
// Ref resolution
// =============================================================================

/// Resolve a parameter to its value by checking ref, literal, default.
pub(crate) fn resolve_param(
    py: Python,
    param: &ParamConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<Option<PyObject>> {
    // Try ref first
    if let Some(ref ref_config) = param.ref_config {
        if let Some(value) = resolve_ref(py, ref_config, state, context)? {
            return Ok(Some(value));
        }
    }

    // Try literal
    if let Some(ref literal) = param.literal {
        return Ok(Some(literal.clone_ref(py)));
    }

    // Try default
    if let Some(ref default) = param.default_value {
        return Ok(Some(default.clone_ref(py)));
    }

    Ok(None)
}

/// Resolve a Ref config to its value from state.
pub(crate) fn resolve_ref(
    py: Python,
    ref_config: &RefConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<Option<PyObject>> {
    // Look up the source value in state
    let value = state.get(py, &ref_config.source, &ref_config.var, context);

    match value {
        Some(val) => {
            if ref_config.ops.is_empty() {
                // No ops to apply — return raw value
                Ok(Some(val.clone_ref(py)))
            } else {
                // Apply ref ops chain
                let result = evaluate_ref_ops(py, val.clone_ref(py), &ref_config.ops, state, context)?;
                Ok(Some(result))
            }
        }
        None => Ok(None),
    }
}

/// Resolve an iteration parameter (each or broadcast) to its value.
pub(crate) fn resolve_iter_param(
    py: Python,
    param: &IterParamConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<Option<PyObject>> {
    if let Some(ref ref_config) = param.ref_config {
        if let Some(value) = resolve_ref(py, ref_config, state, context)? {
            return Ok(Some(value));
        }
    }

    if let Some(ref literal) = param.literal {
        return Ok(Some(literal.clone_ref(py)));
    }

    Ok(None)
}

// =============================================================================
// Result storage and output forwarding
// =============================================================================

/// Store an op's execution result into state.
/// Extracts `$tags` for dynamic tagging (mirrors base.py:679-694).
pub(crate) fn store_result(
    py: Python,
    op: &OpConfig,
    result_obj: Option<PyObject>,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    if let Some(result) = result_obj {
        if let Ok(dict) = result.downcast_bound::<PyDict>(py) {
            for (k, v) in dict.iter() {
                let key: String = k.extract()?;
                if key == "$tags" {
                    // Extract tags and store in state metadata (not as output variable)
                    if let Ok(tag_list) = v.extract::<Vec<String>>() {
                        state.add_tags(tag_list);
                    }
                    continue;
                }
                state.set(op.full_name.clone(), key, context.to_string(), v.unbind());
            }
        }
    }
    Ok(())
}

/// Push output refs — forward op outputs to parent/destination state.
pub(crate) fn push_output_refs(
    py: Python,
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    for param in &op.outputs {
        if let Some(ref ref_config) = param.ref_config {
            if let Some(value) = state.get(py, &op.full_name, &param.var_name, context) {
                let value = value.clone_ref(py);
                state.set(
                    ref_config.source.clone(),
                    ref_config.var.clone(),
                    context.to_string(),
                    value,
                );
            }
        }
    }
    Ok(())
}

/// Call a Python callable for an op.
/// For async ops (is_async=true), drives the returned coroutine to completion.
/// Uses asyncio.run() when no event loop is running, or offloads to a worker
/// thread when called from within an existing event loop (e.g., via Hush.run()).
pub(crate) fn call_python(
    py: Python,
    op: &OpConfig,
    inputs_dict: &Bound<'_, PyDict>,
) -> PyResult<Option<PyObject>> {
    match &op.python_callable {
        Some(callable) => {
            let result = callable.bind(py).call((), Some(inputs_dict))?;
            if op.is_async {
                let driven = drive_coroutine(py, &result)?;
                Ok(Some(driven.unbind()))
            } else {
                Ok(Some(result.unbind()))
            }
        }
        None => Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "Op '{}' has no python_callable and no rust_op",
            op.full_name
        ))),
    }
}

/// Drive an async coroutine to completion.
///
/// - If no event loop is running: uses `asyncio.run(coro)` directly.
/// - If inside a running event loop (e.g., called from Hush's async engine):
///   offloads to a ThreadPoolExecutor worker which creates its own event loop.
///   This avoids the "cannot be called from a running event loop" error.
pub(crate) fn drive_coroutine<'py>(
    py: Python<'py>,
    coro: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let asyncio = py.import_bound("asyncio")?;

    // Check if there's already a running event loop
    let in_running_loop = asyncio.call_method0("get_running_loop").is_ok();

    if in_running_loop {
        // Offload to a worker thread that creates its own event loop
        let cf = py.import_bound("concurrent.futures")?;
        let executor = cf.getattr("ThreadPoolExecutor")?.call1((1i32,))?;
        let asyncio_run = asyncio.getattr("run")?;
        let future = executor.call_method1("submit", (&asyncio_run, coro))?;
        let output = future.call_method0("result")?;
        let _ = executor.call_method0("shutdown");
        Ok(output)
    } else {
        // No running loop — safe to use asyncio.run() directly
        asyncio.call_method1("run", (coro,))
    }
}

// =============================================================================
// Native provider op execution (GIL-free async HTTP)
// =============================================================================

/// Execute a native provider op via rush-providers.
///
/// Flow:
/// 1. Extract inputs from PyDict to serde_json::Value (with GIL)
/// 2. Release GIL, run async HTTP via tokio runtime (no Python involved)
/// 3. Acquire GIL, convert outputs back to PyDict
///
/// This is the key performance win: HTTP I/O happens entirely without GIL,
/// enabling true concurrent I/O across parallel ops.
fn execute_provider_op(
    py: Python,
    op: &OpConfig,
    inputs_dict: &Bound<'_, PyDict>,
) -> PyResult<Option<PyObject>> {
    let config = match &op.provider_config {
        Some(c) => c,
        None => return call_python(py, op, inputs_dict),
    };

    if !is_native_config(config) {
        return call_python(py, op, inputs_dict);
    }

    // 1. Extract inputs to serde_json::Value (with GIL)
    let json_inputs =
        rush_providers::py_serde::pydict_to_json(py, inputs_dict).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "Failed to serialize inputs for native provider op: {}",
                e
            ))
        })?;

    // 2. Release GIL and run async HTTP via tokio
    let json_outputs = py.allow_threads(|| {
        runtime::get_runtime().block_on(async {
            rush_providers::ops::execute(&op.op_type, json_inputs, config).await
        })
    });

    match json_outputs {
        Ok(outputs) => {
            // 3. Convert back to PyDict (with GIL)
            let py_result = rush_providers::py_serde::json_to_pydict(py, &outputs)?;
            Ok(Some(py_result))
        }
        Err(e) => {
            Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Native provider op error: {}",
                e
            )))
        }
    }
}

/// Check if a provider config's api_type(s) are natively supported.
fn is_native_config(config: &rush_providers::config::ProviderConfig) -> bool {
    match config {
        rush_providers::config::ProviderConfig::LLM(c) => {
            !c.configs.is_empty()
                && c.configs.iter().all(|cfg| {
                    rush_providers::ops::is_native_provider_op(cfg.api_type())
                })
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
// Streaming native provider op execution
// =============================================================================

/// Wrapper to send a raw pointer across threads.
/// SAFETY: The caller must guarantee the pointee outlives the spawned task.
struct SendConfigPtr(*const rush_providers::config::ProviderConfig);
unsafe impl Send for SendConfigPtr {}
unsafe impl Sync for SendConfigPtr {}

impl SendConfigPtr {
    fn get(&self) -> &rush_providers::config::ProviderConfig {
        // SAFETY: Guaranteed valid by construction (points to GraphConfig-owned data)
        unsafe { &*self.0 }
    }
}

/// Execute a native LLM provider op with streaming via rush-providers.
///
/// Flow:
/// 1. Extract inputs to JSON (with GIL)
/// 2. Get STREAM_SERVICE reference and streaming metadata
/// 3. Create std::sync::mpsc channel for chunks
/// 4. Spawn async streaming HTTP in tokio (GIL-free)
/// 5. Poll channel for chunks, push each to STREAM_SERVICE (with GIL)
/// 6. Signal end to STREAM_SERVICE
/// 7. Return accumulated result as PyDict
///
/// The GIL is briefly released between chunk polls to allow other Python threads
/// to make progress, while HTTP I/O runs entirely GIL-free in tokio.
fn execute_provider_op_streaming(
    py: Python,
    op: &OpConfig,
    inputs_dict: &Bound<'_, PyDict>,
    state: &EngineState,
    context: &str,
) -> PyResult<Option<PyObject>> {
    let config = match &op.provider_config {
        Some(c) => c,
        None => return call_python(py, op, inputs_dict),
    };

    if !is_native_config(config) {
        return call_python(py, op, inputs_dict);
    }

    // 1. Extract inputs to serde_json::Value (with GIL)
    let json_inputs =
        rush_providers::py_serde::pydict_to_json(py, inputs_dict).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "Failed to serialize inputs for streaming provider op: {}",
                e
            ))
        })?;

    // 2. Get STREAM_SERVICE and streaming metadata
    let hush_core = py.import_bound("hush.core")?;
    let stream_service = hush_core.getattr("STREAM_SERVICE")?;

    let request_id = state.request_id().unwrap_or_else(|| "default".to_string());

    // channel_name = "{full_name}[{context_id or 'main'}]" — mirrors Python's BaseOp.identity()
    let ctx_label = if context.is_empty() { "main" } else { context };
    let channel_name = format!("{}[{}]", op.full_name, ctx_label);

    // 3. Create channel for chunk forwarding (std::sync::mpsc)
    let (chunk_tx, chunk_rx) = mpsc::channel::<serde_json::Value>();

    // 4. Spawn streaming HTTP in tokio (GIL-free)
    let op_type = op.op_type.clone();
    // SAFETY: config lives for the duration of the engine run (owned by GraphConfig).
    // The spawned task completes before we return from this function (we await it).
    let config_ptr = SendConfigPtr(config as *const _);

    let handle = runtime::get_runtime().spawn(async move {
        let config_ref = config_ptr.get();
        rush_providers::ops::execute_streaming(&op_type, json_inputs, config_ref, chunk_tx).await
    });

    // 5. Pump chunks from channel to STREAM_SERVICE
    //    Use try_recv() (non-blocking) + brief GIL release to avoid blocking Python
    loop {
        match chunk_rx.try_recv() {
            Ok(chunk_json) => {
                // Convert JSON chunk to Python dict and push to STREAM_SERVICE
                let py_chunk = rush_providers::py_serde::json_to_py(py, &chunk_json)?;

                // STREAM_SERVICE.push() is async — drive the coroutine
                let push_coro = stream_service.call_method1(
                    "push",
                    (&request_id, &channel_name, py_chunk.bind(py)),
                )?;
                drive_coroutine(py, &push_coro)?;
            }
            Err(mpsc::TryRecvError::Empty) => {
                // No chunk ready — check if streaming task has finished
                if handle.is_finished() {
                    // Drain any remaining chunks that arrived before we checked
                    while let Ok(chunk_json) = chunk_rx.try_recv() {
                        let py_chunk = rush_providers::py_serde::json_to_py(py, &chunk_json)?;
                        let push_coro = stream_service.call_method1(
                            "push",
                            (&request_id, &channel_name, py_chunk.bind(py)),
                        )?;
                        drive_coroutine(py, &push_coro)?;
                    }
                    break;
                }
                // Release GIL briefly to let other Python threads progress
                py.allow_threads(|| std::thread::sleep(Duration::from_millis(1)));
            }
            Err(mpsc::TryRecvError::Disconnected) => {
                // Sender dropped — stream ended
                break;
            }
        }
    }

    // 6. Signal end of stream to STREAM_SERVICE
    let end_coro = stream_service.call_method1("end", (&request_id, &channel_name))?;
    drive_coroutine(py, &end_coro)?;

    // 7. Get the accumulated final result from the tokio task
    let final_result = py.allow_threads(|| {
        runtime::get_runtime().block_on(async { handle.await })
    });

    match final_result {
        Ok(Ok(outputs)) => {
            let py_result = rush_providers::py_serde::json_to_pydict(py, &outputs)?;
            Ok(Some(py_result))
        }
        Ok(Err(e)) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "Streaming provider op error: {}",
            e
        ))),
        Err(e) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "Streaming task panicked: {}",
            e
        ))),
    }
}
