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
use crate::ops::iteration::helpers;
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

    // 2. Resolve each/broadcast values and determine iteration count
    let each_values = helpers::resolve_each_values(py, iter_config, state, context)?;
    let broadcast_values = helpers::resolve_broadcast_values(py, iter_config, state, context)?;
    let n = helpers::determine_iteration_count(py, &op.full_name, &each_values, &broadcast_values)?;

    if n == 0 {
        helpers::store_empty_results(py, op, state, context)?;
        return Ok(());
    }

    // 3. Pre-extract each[var][i] items (need GIL to index Python lists)
    let mut per_iter_each: Vec<Vec<(String, PyObject)>> = Vec::with_capacity(n);
    for i in 0..n {
        let mut items = Vec::with_capacity(each_values.len());
        for (var_name, list_val) in &each_values {
            let item = list_val.bind(py).get_item(i)?;
            items.push((var_name.clone(), item.unbind()));
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
    let results: Vec<Mutex<Option<Result<PyObject, String>>>> =
        (0..n).map(|_| Mutex::new(None)).collect();
    let should_stop = AtomicBool::new(false);

    // 5. Process in chunks of max_concurrency using rayon
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

    // 6. Check fail_fast — propagate first error
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

    // 7. Build result list
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
                let error_dict = PyDict::new_bound(py);
                error_dict.set_item("error", "Skipped due to fail_fast")?;
                error_dict.set_item("error_type", "Skipped")?;
                result_objects.push(error_dict.unbind().into());
            }
        }
    }

    // 8. Transpose results and store
    transpose_and_store(py, op, &result_objects, state, context)?;

    // 9. Add iteration_metrics and push output refs
    helpers::store_iteration_metrics(py, op, state, context, n, success_count, n - success_count)?;
    base::push_output_refs(py, op, state, context)?;

    Ok(())
}

// =============================================================================
// Helpers (shared with aiter_op and for_op)
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
