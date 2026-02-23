//! AIterOp execution — async streaming iteration.
//!
//! Mirrors Python's `ops/iteration/aiter_op.py` (AIterOp).
//! Consumes an async iterable source, optionally batches items,
//! processes each batch/item by running the inner graph, and
//! calls an optional callback in order.
//!
//! In Rust mode, items are collected from the async iterable first
//! (losing true streaming), then processed concurrently with rayon.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rayon::prelude::*;

use crate::config::OpConfig;
use crate::ops::base;
use crate::ops::graph::graph_op;
use crate::ops::iteration::map_op;
use crate::states::state::EngineState;

/// Execute an AIterOp: collect async items → batch → process → callback.
pub(crate) fn run(
    py: Python,
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    let inner = op.inner_graph.as_ref().ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "AIterOp '{}' missing inner_graph config",
            op.full_name
        ))
    })?;
    let iter_config = op.iteration_config.as_ref().ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "AIterOp '{}' missing iteration_config",
            op.full_name
        ))
    })?;

    // 1. Resolve op inputs from parent context
    for param in &op.inputs {
        if let Some(value) = base::resolve_param(py, param, state, context)? {
            state.set(
                op.full_name.clone(),
                param.var_name.clone(),
                context.to_string(),
                value,
            );
        }
    }

    // 2. Resolve the async iterable source (exactly one each param)
    let source_param = iter_config.each.first().ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "AIterOp '{}' requires exactly one 'each' parameter",
            op.full_name
        ))
    })?;
    let source = base::resolve_iter_param(py, source_param, state, context)?.ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "AIterOp '{}' could not resolve source '{}'",
            op.full_name, source_param.var_name
        ))
    })?;
    let source_var = &source_param.var_name;

    // 3. Resolve broadcast values
    let mut broadcast_values: Vec<(String, PyObject)> = Vec::new();
    for param in &iter_config.broadcast {
        if let Some(value) = base::resolve_iter_param(py, param, state, context)? {
            broadcast_values.push((param.var_name.clone(), value));
        }
    }

    // 4. Collect all items from async iterable
    let items = collect_async_items(py, &source)?;

    // 5. Apply batching if batch_fn is provided
    let chunks: Vec<PyObject> = if let Some(ref batch_fn) = iter_config.batch_fn {
        apply_batching(py, &items, batch_fn)?
    } else {
        // Each item is its own chunk
        items
    };

    let n = chunks.len();
    if n == 0 {
        store_empty_results(py, op, state, context)?;
        return Ok(());
    }

    // 6. Process chunks concurrently
    let max_concurrency = iter_config
        .max_concurrency
        .unwrap_or_else(|| std::thread::available_parallelism().map(|p| p.get()).unwrap_or(4));
    let ctx_prefix = if context.is_empty() {
        String::new()
    } else {
        format!("{}.", context)
    };

    let results: Vec<Mutex<Option<Result<PyObject, String>>>> =
        (0..n).map(|_| Mutex::new(None)).collect();
    let should_stop = AtomicBool::new(false);

    for chunk_start in (0..n).step_by(max_concurrency) {
        if should_stop.load(Ordering::Relaxed) {
            break;
        }

        let chunk_end = (chunk_start + max_concurrency).min(n);
        let indices: Vec<usize> = (chunk_start..chunk_end).collect();

        py.allow_threads(|| {
            indices.par_iter().for_each(|&i| {
                if should_stop.load(Ordering::Relaxed) {
                    return;
                }

                Python::with_gil(|py| {
                    let iter_context = format!("{}[{}]", ctx_prefix, i);

                    // Store the chunk item in state
                    state.set(
                        op.full_name.clone(),
                        source_var.clone(),
                        iter_context.clone(),
                        chunks[i].clone_ref(py),
                    );

                    // Store broadcast values
                    for (var_name, val) in &broadcast_values {
                        state.set(
                            op.full_name.clone(),
                            var_name.clone(),
                            iter_context.clone(),
                            val.clone_ref(py),
                        );
                    }

                    // Run inner graph + collect outputs
                    let iter_result = graph_op::run_graph(py, inner, state, &iter_context)
                        .and_then(|_| graph_op::get_outputs(py, inner, state, &iter_context));

                    match iter_result {
                        Ok(output) => {
                            *results[i].lock().unwrap() = Some(Ok(output));
                        }
                        Err(err) => {
                            let err_msg = format!("{}", err);
                            // AIterOp always continues (no fail_fast)
                            let _ = (|| -> PyResult<()> {
                                let logging = py.import_bound("logging")?;
                                let logger =
                                    logging.call_method1("getLogger", ("hush.core",))?;
                                logger.call_method1(
                                    "warning",
                                    (format!(
                                        "[rush] AIterOp '{}' chunk {} failed: {}",
                                        op.full_name, i, err_msg
                                    ),),
                                )?;
                                Ok(())
                            })();
                            *results[i].lock().unwrap() = Some(Err(err_msg));
                        }
                    }
                });
            });
        });
    }

    // 7. Build result list (in order)
    let mut result_objects: Vec<PyObject> = Vec::with_capacity(n);
    let mut success_count: usize = 0;
    let mut callback_errors: usize = 0;

    for i in 0..n {
        match results[i].lock().unwrap().take() {
            Some(Ok(output)) => {
                // Call callback if provided (in order, sequentially)
                if let Some(ref callback) = iter_config.callback {
                    if let Err(_err) = call_callback(py, callback, &output) {
                        callback_errors += 1;
                        let _ = (|| -> PyResult<()> {
                            let logging = py.import_bound("logging")?;
                            let logger = logging.call_method1("getLogger", ("hush.core",))?;
                            logger.call_method1(
                                "warning",
                                (format!(
                                    "[rush] AIterOp '{}' callback error at chunk {}: {}",
                                    op.full_name, i, _err
                                ),),
                            )?;
                            Ok(())
                        })();
                    }
                }
                result_objects.push(output);
                success_count += 1;
            }
            Some(Err(err_msg)) => {
                let error_dict = PyDict::new_bound(py);
                error_dict.set_item("error", &err_msg)?;
                result_objects.push(error_dict.unbind().into());
            }
            None => {
                let error_dict = PyDict::new_bound(py);
                error_dict.set_item("error", "Skipped")?;
                result_objects.push(error_dict.unbind().into());
            }
        }
    }

    // 8. Transpose results and store
    map_op::transpose_and_store(py, op, &result_objects, state, context)?;

    // 9. Add iteration_metrics
    let metrics = PyDict::new_bound(py);
    metrics.set_item("total_iterations", n)?;
    metrics.set_item("success_count", success_count)?;
    metrics.set_item("error_count", n - success_count)?;
    if iter_config.callback.is_some() {
        let cb_metrics = PyDict::new_bound(py);
        cb_metrics.set_item("error_count", callback_errors)?;
        metrics.set_item("callback", cb_metrics)?;
    }
    state.set(
        op.full_name.clone(),
        "iteration_metrics".to_string(),
        context.to_string(),
        metrics.unbind().into(),
    );

    // 10. Push output refs
    base::push_output_refs(py, op, state, context)?;

    Ok(())
}

// =============================================================================
// Helpers
// =============================================================================

/// Collect all items from a Python async iterable into a Vec.
/// Uses a Python helper to drive `async for item in source`.
fn collect_async_items(py: Python, source: &PyObject) -> PyResult<Vec<PyObject>> {
    let builtins = py.import_bound("builtins")?;
    let globals = PyDict::new_bound(py);
    globals.set_item("__builtins__", &builtins)?;
    globals.set_item("_src", source.bind(py))?;

    py.run_bound(
        concat!(
            "async def _collect():\n",
            "    _items = []\n",
            "    async for item in _src:\n",
            "        _items.append(item)\n",
            "    return _items\n",
            "_coro = _collect()\n",
        ),
        Some(&globals),
        None,
    )?;

    let coro = globals.get_item("_coro")?.ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("Failed to create collect coroutine")
    })?;

    let result = base::drive_coroutine(py, &coro)?;
    let list: &Bound<'_, PyList> = result.downcast()?;
    Ok(list.iter().map(|item| item.unbind()).collect())
}

/// Apply batching: group items using batch_fn(batch, new_item) → bool.
/// When batch_fn returns True, the current batch is flushed.
fn apply_batching(
    py: Python,
    items: &[PyObject],
    batch_fn: &PyObject,
) -> PyResult<Vec<PyObject>> {
    let mut batches: Vec<PyObject> = Vec::new();
    let mut current_batch: Vec<PyObject> = Vec::new();

    for item in items {
        if !current_batch.is_empty() {
            let batch_list =
                PyList::new_bound(py, current_batch.iter().map(|b| b.bind(py)));
            let should_flush: bool = batch_fn
                .bind(py)
                .call1((&batch_list, item.bind(py)))?
                .extract()?;
            if should_flush {
                let flushed = PyList::new_bound(py, current_batch.iter().map(|b| b.bind(py)));
                batches.push(flushed.unbind().into());
                current_batch.clear();
            }
        }
        current_batch.push(item.clone_ref(py));
    }

    // Flush remaining items
    if !current_batch.is_empty() {
        let flushed = PyList::new_bound(py, current_batch.iter().map(|b| b.bind(py)));
        batches.push(flushed.unbind().into());
    }

    Ok(batches)
}

/// Call the async callback with a result dict.
fn call_callback(py: Python, callback: &PyObject, result: &PyObject) -> PyResult<()> {
    let coro = callback.bind(py).call1((result.bind(py),))?;
    // Drive the async callback
    let _ = base::drive_coroutine(py, &coro)?;
    Ok(())
}

/// Store empty results for zero-item case.
fn store_empty_results(
    py: Python,
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    let output_keys: Vec<&str> = op
        .outputs
        .iter()
        .filter(|p| p.var_name != "iteration_metrics")
        .map(|p| p.var_name.as_str())
        .collect();

    for key in &output_keys {
        let empty = PyList::empty_bound(py);
        state.set(
            op.full_name.clone(),
            key.to_string(),
            context.to_string(),
            empty.unbind().into(),
        );
    }

    let metrics = PyDict::new_bound(py);
    metrics.set_item("total_iterations", 0)?;
    metrics.set_item("success_count", 0)?;
    metrics.set_item("error_count", 0)?;
    state.set(
        op.full_name.clone(),
        "iteration_metrics".to_string(),
        context.to_string(),
        metrics.unbind().into(),
    );

    base::push_output_refs(py, op, state, context)?;

    Ok(())
}
