//! Base op execution — leaf ops, ref resolution, result storage.
//!
//! Mirrors Python's `ops/base.py` (BaseOp.run, store_result, resolve).
//! Includes observability: enabled flag, per-op timing, $tags, verbose logging,
//! and slow op warnings.

use std::sync::mpsc;
use std::time::{Duration, Instant};

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::config::{IterParamConfig, OpConfig, ParamConfig, RefConfig};
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
/// Mirrors Python's BaseOp.run() try/except/finally pattern.
pub(crate) fn run(
    py: Python,
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    if !op.enabled {
        return Ok(());
    }

    // Start timing
    let datetime_mod = py.import_bound("datetime")?;
    let datetime_cls = datetime_mod.getattr("datetime")?;
    let start_time = datetime_cls.call_method0("now")?;
    let perf_start = Instant::now();

    // Try: resolve inputs → execute → store outputs
    let exec_result: PyResult<()> = (|| {
        let inputs_dict = PyDict::new_bound(py);
        for param in &op.inputs {
            if let Some(value) = resolve_param(py, param, state, context)? {
                inputs_dict.set_item(&param.var_name, value.bind(py))?;
            }
        }

        let result_obj = execute_op(py, op, &inputs_dict, state, context)?;
        store_result(py, op, result_obj, state, context)?;

        Ok(())
    })();

    // "finally" block — always runs
    let duration_ms = store_timing(py, op, state, context, start_time, perf_start)?;

    if let Err(ref err) = exec_result {
        log_error(py, op, state, context, &format!("{}", err))?;
    }

    if duration_ms > 100.0 {
        warn_slow_op(py, op, duration_ms)?;
    }

    if op.verbose {
        log_verbose(py, op, duration_ms)?;
    }

    if exec_result.is_ok() {
        push_output_refs(py, op, state, context)?;
    }

    Ok(())
}

// =============================================================================
// Execution dispatch
// =============================================================================

/// How an op should be executed — classified once, matched once.
enum OpRoute<'a> {
    /// Streaming LLM provider (SSE chunked response).
    StreamingProvider,
    /// Non-streaming provider (LLM, embedding, rerank).
    Provider,
    /// Native transform op (prompt, parser) — GIL-free, synchronous.
    NativeTransform,
    /// Plugin op from a cdylib shared library ("path/to/lib.so::func").
    Plugin(&'a str),
    /// Fallback to Python callable.
    Python,
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
    OpRoute::Python
}

/// Dispatch op execution to the appropriate handler.
///
/// Priority: streaming provider → provider → native transform → plugin → python.
fn execute_op(
    py: Python,
    op: &OpConfig,
    inputs_dict: &Bound<'_, PyDict>,
    state: &EngineState,
    context: &str,
) -> PyResult<Option<PyObject>> {
    match classify_op(op) {
        OpRoute::StreamingProvider => execute_provider_op_streaming(py, op, inputs_dict, state, context),
        OpRoute::Provider          => execute_provider_op(py, op, inputs_dict),
        OpRoute::NativeTransform   => execute_native_transform_op(py, &op.op_type, inputs_dict),
        OpRoute::Plugin(spec)      => execute_plugin_op(py, spec, inputs_dict),
        OpRoute::Python            => call_python(py, op, inputs_dict),
    }
}

// =============================================================================
// Observability helpers
// =============================================================================

/// Store timing metrics (start_time, end_time, duration_ms) in state.
fn store_timing(
    py: Python,
    op: &OpConfig,
    state: &EngineState,
    context: &str,
    start_time: Bound<'_, PyAny>,
    perf_start: Instant,
) -> PyResult<f64> {
    let duration_ms = perf_start.elapsed().as_secs_f64() * 1000.0;
    let datetime_mod = py.import_bound("datetime")?;
    let datetime_cls = datetime_mod.getattr("datetime")?;
    let end_time = datetime_cls.call_method0("now")?;

    state.set(op.full_name.clone(), "start_time".to_string(), context.to_string(), start_time.unbind());
    state.set(op.full_name.clone(), "end_time".to_string(), context.to_string(), end_time.unbind());
    state.set(op.full_name.clone(), "duration_ms".to_string(), context.to_string(), duration_ms.to_object(py));

    Ok(duration_ms)
}

/// Log an error and store it in state.
fn log_error(
    py: Python,
    op: &OpConfig,
    state: &EngineState,
    context: &str,
    error_msg: &str,
) -> PyResult<()> {
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
        (format!("[rush] Error in op {}: {}", op.full_name, error_msg),),
    )?;
    Ok(())
}

/// Emit a slow-op warning (>100ms).
fn warn_slow_op(py: Python, op: &OpConfig, duration_ms: f64) -> PyResult<()> {
    let warnings = py.import_bound("warnings")?;
    warnings.call_method1(
        "warn",
        (format!("Slow op {}: {:.1}ms", op.full_name, duration_ms),),
    )?;
    Ok(())
}

/// Log verbose op execution info.
fn log_verbose(py: Python, op: &OpConfig, duration_ms: f64) -> PyResult<()> {
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
    if let Some(ref ref_config) = param.ref_config {
        if let Some(value) = resolve_ref(py, ref_config, state, context)? {
            return Ok(Some(value));
        }
    }

    if let Some(ref literal) = param.literal {
        return Ok(Some(literal.clone_ref(py)));
    }

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
    let value = state.get(py, &ref_config.source, &ref_config.var, context);

    match value {
        Some(val) => {
            if ref_config.ops.is_empty() {
                Ok(Some(val.clone_ref(py)))
            } else {
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

// =============================================================================
// Plugin op execution
// =============================================================================

/// Execute a plugin op from a cdylib shared library.
fn execute_plugin_op(
    py: Python,
    spec: &str,
    inputs_dict: &Bound<'_, PyDict>,
) -> PyResult<Option<PyObject>> {
    let (lib_path, func_name) = plugins::parse_plugin_spec(spec).ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "Invalid plugin spec '{}': expected 'path/to/lib.so::func_name'",
            spec
        ))
    })?;

    let json_value = rush_providers::py_serde::pydict_to_json(py, inputs_dict).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "Failed to serialize inputs for plugin op: {}",
            e
        ))
    })?;
    let json_bytes = serde_json::to_vec(&json_value).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "Failed to serialize JSON: {}",
            e
        ))
    })?;

    let result_value = py.allow_threads(|| plugins::load_and_call(lib_path, func_name, &json_bytes));

    match result_value {
        Ok(output_json) => {
            let py_result = rush_providers::py_serde::json_to_py(py, &output_json)?;
            Ok(Some(py_result))
        }
        Err(e) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "Plugin op error: {}",
            e
        ))),
    }
}

// =============================================================================
// Native transform op execution (GIL-free, synchronous)
// =============================================================================

/// Execute a native transform op (prompt, parser) via rush-providers.
///
/// These are pure CPU ops that don't need provider config or async HTTP.
/// Runs GIL-free for maximum performance.
fn execute_native_transform_op(
    py: Python,
    op_type: &str,
    inputs_dict: &Bound<'_, PyDict>,
) -> PyResult<Option<PyObject>> {
    let json_inputs =
        rush_providers::py_serde::pydict_to_json(py, inputs_dict).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "Failed to serialize inputs for native transform op: {}",
                e
            ))
        })?;

    let op_type_owned = op_type.to_string();
    let json_outputs = py.allow_threads(|| {
        rush_providers::ops::execute_transform(&op_type_owned, json_inputs)
    });

    match json_outputs {
        Ok(outputs) => {
            let py_result = rush_providers::py_serde::json_to_pydict(py, &outputs)?;
            Ok(Some(py_result))
        }
        Err(e) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "Native transform op error: {}",
            e
        ))),
    }
}

// =============================================================================
// Python callable execution
// =============================================================================

/// Call a Python callable for an op.
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
pub(crate) fn drive_coroutine<'py>(
    py: Python<'py>,
    coro: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let asyncio = py.import_bound("asyncio")?;
    let in_running_loop = asyncio.call_method0("get_running_loop").is_ok();

    if in_running_loop {
        let cf = py.import_bound("concurrent.futures")?;
        let executor = cf.getattr("ThreadPoolExecutor")?.call1((1i32,))?;
        let asyncio_run = asyncio.getattr("run")?;
        let future = executor.call_method1("submit", (&asyncio_run, coro))?;
        let output = future.call_method0("result")?;
        let _ = executor.call_method0("shutdown");
        Ok(output)
    } else {
        asyncio.call_method1("run", (coro,))
    }
}

// =============================================================================
// Native provider op execution (GIL-free async HTTP)
// =============================================================================

/// Execute a native provider op via rush-providers.
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

    let json_inputs =
        rush_providers::py_serde::pydict_to_json(py, inputs_dict).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "Failed to serialize inputs for native provider op: {}",
                e
            ))
        })?;

    let json_outputs = py.allow_threads(|| {
        runtime::block_on_async(async {
            rush_providers::ops::execute(&op.op_type, json_inputs, config).await
        })
    });

    match json_outputs {
        Ok(outputs) => {
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
struct SendConfigPtr(*const rush_providers::config::ProviderConfig);
unsafe impl Send for SendConfigPtr {}
unsafe impl Sync for SendConfigPtr {}

impl SendConfigPtr {
    fn get(&self) -> &rush_providers::config::ProviderConfig {
        unsafe { &*self.0 }
    }
}

/// Execute a native LLM provider op with streaming via rush-providers.
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

    // 1. Extract inputs to JSON
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
    let ctx_label = if context.is_empty() { "main" } else { context };
    let channel_name = format!("{}[{}]", op.full_name, ctx_label);

    // 3. Create channel and spawn async streaming HTTP
    let (chunk_tx, chunk_rx) = mpsc::channel::<serde_json::Value>();
    let op_type = op.op_type.clone();
    let config_ptr = SendConfigPtr(config as *const _);

    let handle = runtime::get_runtime().spawn(async move {
        let config_ref = config_ptr.get();
        rush_providers::ops::execute_streaming(&op_type, json_inputs, config_ref, chunk_tx).await
    });

    // 4. Pump chunks from channel to STREAM_SERVICE
    pump_chunks(py, &chunk_rx, &handle, &stream_service, &request_id, &channel_name)?;

    // 5. Signal end of stream
    let end_coro = stream_service.call_method1("end", (&request_id, &channel_name))?;
    drive_coroutine(py, &end_coro)?;

    // 6. Get final result from tokio task
    let final_result = py.allow_threads(|| {
        runtime::block_on_async(async { handle.await })
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

/// Pump chunks from an mpsc channel to the Python STREAM_SERVICE.
///
/// Uses try_recv() (non-blocking) + brief GIL release between polls
/// to avoid blocking Python threads while HTTP I/O runs GIL-free.
fn pump_chunks(
    py: Python,
    chunk_rx: &mpsc::Receiver<serde_json::Value>,
    handle: &tokio::task::JoinHandle<Result<serde_json::Value, rush_providers::http::ProviderError>>,
    stream_service: &Bound<'_, PyAny>,
    request_id: &str,
    channel_name: &str,
) -> PyResult<()> {
    loop {
        match chunk_rx.try_recv() {
            Ok(chunk_json) => {
                let py_chunk = rush_providers::py_serde::json_to_py(py, &chunk_json)?;
                let push_coro = stream_service.call_method1(
                    "push",
                    (request_id, channel_name, py_chunk.bind(py)),
                )?;
                drive_coroutine(py, &push_coro)?;
            }
            Err(mpsc::TryRecvError::Empty) => {
                if handle.is_finished() {
                    // Drain remaining chunks
                    while let Ok(chunk_json) = chunk_rx.try_recv() {
                        let py_chunk = rush_providers::py_serde::json_to_py(py, &chunk_json)?;
                        let push_coro = stream_service.call_method1(
                            "push",
                            (request_id, channel_name, py_chunk.bind(py)),
                        )?;
                        drive_coroutine(py, &push_coro)?;
                    }
                    break;
                }
                py.allow_threads(|| std::thread::sleep(Duration::from_millis(1)));
            }
            Err(mpsc::TryRecvError::Disconnected) => {
                break;
            }
        }
    }
    Ok(())
}
