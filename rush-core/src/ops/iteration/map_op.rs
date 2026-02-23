//! MapOp execution — parallel iteration with concurrency control.
//!
//! Mirrors Python's `ops/iteration/map_op.py` (MapOp).
//! Unlike ForOp (sequential), MapOp runs iterations concurrently using rayon,
//! with max_concurrency limiting the parallelism level.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rayon::prelude::*;

use crate::config::OpConfig;
use crate::ops::base;
use crate::ops::graph::graph_op;
use crate::states::state::EngineState;

/// Execute a MapOp: resolve each/broadcast → iterate concurrently → transpose results.
pub(crate) fn run(
    py: Python,
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    let inner = op.inner_graph.as_ref().ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "MapOp '{}' missing inner_graph config",
            op.full_name
        ))
    })?;
    let iter_config = op.iteration_config.as_ref().ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "MapOp '{}' missing iteration_config",
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

    // 2. Resolve each values (lists) and broadcast values
    let mut each_values: Vec<(String, PyObject)> = Vec::new();
    for param in &iter_config.each {
        if let Some(value) = base::resolve_iter_param(py, param, state, context)? {
            each_values.push((param.var_name.clone(), value));
        }
    }

    let mut broadcast_values: Vec<(String, PyObject)> = Vec::new();
    for param in &iter_config.broadcast {
        if let Some(value) = base::resolve_iter_param(py, param, state, context)? {
            broadcast_values.push((param.var_name.clone(), value));
        }
    }

    // 3. Determine iteration count and validate equal lengths
    let n = if each_values.is_empty() {
        if broadcast_values.is_empty() {
            0
        } else {
            1
        }
    } else {
        let first_len = each_values[0].1.bind(py).len()?;
        for (var_name, val) in &each_values[1..] {
            let this_len = val.bind(py).len()?;
            if this_len != first_len {
                return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                    "MapOp '{}': each variables have different lengths: '{}' has {}, '{}' has {}",
                    op.full_name, each_values[0].0, first_len, var_name, this_len
                )));
            }
        }
        first_len
    };

    if n == 0 {
        // No iterations — store empty results and metrics
        store_empty_results(py, op, state, context)?;
        return Ok(());
    }

    // 4. Pre-extract each[var][i] items (need GIL to index Python lists)
    let mut per_iter_each: Vec<Vec<(String, PyObject)>> = Vec::with_capacity(n);
    for i in 0..n {
        let mut items = Vec::with_capacity(each_values.len());
        for (var_name, list_val) in &each_values {
            let item = list_val.bind(py).get_item(i)?;
            items.push((var_name.clone(), item.unbind()));
        }
        per_iter_each.push(items);
    }

    // 5. Set up concurrent execution
    let max_concurrency = iter_config
        .max_concurrency
        .unwrap_or_else(|| std::thread::available_parallelism().map(|p| p.get()).unwrap_or(4));
    let fail_fast = iter_config.fail_fast;
    let ctx_prefix = if context.is_empty() {
        String::new()
    } else {
        format!("{}.", context)
    };

    // Result storage (thread-safe)
    let results: Vec<Mutex<Option<Result<PyObject, String>>>> =
        (0..n).map(|_| Mutex::new(None)).collect();
    let should_stop = AtomicBool::new(false);

    // 6. Process in chunks of max_concurrency using rayon
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

                    // Store each items for this iteration
                    for (var_name, item) in &per_iter_each[i] {
                        state.set(
                            op.full_name.clone(),
                            var_name.clone(),
                            iter_context.clone(),
                            item.clone_ref(py),
                        );
                    }

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
                            if fail_fast {
                                should_stop.store(true, Ordering::Relaxed);
                            } else {
                                // Log error (best-effort)
                                let _ = (|| -> PyResult<()> {
                                    let logging = py.import_bound("logging")?;
                                    let logger =
                                        logging.call_method1("getLogger", ("hush.core",))?;
                                    logger.call_method1(
                                        "warning",
                                        (format!(
                                            "[rush] MapOp '{}' iteration {} failed: {}",
                                            op.full_name, i, err_msg
                                        ),),
                                    )?;
                                    Ok(())
                                })();
                            }
                            *results[i].lock().unwrap() = Some(Err(err_msg));
                        }
                    }
                });
            });
        });
    }

    // 7. Check fail_fast — propagate first error
    if fail_fast && should_stop.load(Ordering::Relaxed) {
        for i in 0..n {
            if let Some(Err(ref msg)) = *results[i].lock().unwrap() {
                return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                    "MapOp '{}' iteration {} failed: {}",
                    op.full_name, i, msg
                )));
            }
        }
    }

    // 8. Build result list
    let mut result_objects: Vec<PyObject> = Vec::with_capacity(n);
    let mut success_count: usize = 0;

    for i in 0..n {
        match results[i].lock().unwrap().take() {
            Some(Ok(output)) => {
                result_objects.push(output);
                success_count += 1;
            }
            Some(Err(err_msg)) => {
                let error_dict = PyDict::new_bound(py);
                error_dict.set_item("error", &err_msg)?;
                error_dict.set_item("error_type", "PyErr")?;
                result_objects.push(error_dict.unbind().into());
            }
            None => {
                // Iteration was skipped (fail_fast stopped early)
                let error_dict = PyDict::new_bound(py);
                error_dict.set_item("error", "Skipped due to fail_fast")?;
                error_dict.set_item("error_type", "Skipped")?;
                result_objects.push(error_dict.unbind().into());
            }
        }
    }

    // 9. Transpose results: [{a:1,b:2}, {a:3,b:4}] → {a:[1,3], b:[2,4]}
    transpose_and_store(py, op, &result_objects, state, context)?;

    // 10. Add iteration_metrics
    let metrics = PyDict::new_bound(py);
    metrics.set_item("total_iterations", n)?;
    metrics.set_item("success_count", success_count)?;
    metrics.set_item("error_count", n - success_count)?;
    state.set(
        op.full_name.clone(),
        "iteration_metrics".to_string(),
        context.to_string(),
        metrics.unbind().into(),
    );

    // 11. Push output refs
    base::push_output_refs(py, op, state, context)?;

    Ok(())
}

// =============================================================================
// Helpers (shared with aiter_op)
// =============================================================================

/// Transpose result dicts and store each key as a list in state.
pub(super) fn transpose_and_store(
    py: Python,
    op: &OpConfig,
    results: &[PyObject],
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
        let list = PyList::empty_bound(py);
        for r in results {
            if let Ok(dict) = r.downcast_bound::<PyDict>(py) {
                match dict.get_item(key)? {
                    Some(val) => list.append(val)?,
                    None => list.append(py.None())?,
                }
            } else {
                list.append(py.None())?;
            }
        }
        state.set(
            op.full_name.clone(),
            key.to_string(),
            context.to_string(),
            list.unbind().into(),
        );
    }

    Ok(())
}

/// Store empty results for zero-iteration case.
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
