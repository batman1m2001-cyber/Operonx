//! ForOp execution — iterate over lists with broadcast support.
//!
//! Mirrors Python's `ops/iteration/for_op.py` (ForOp).

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::config::OpConfig;
use crate::ops::base;
use crate::ops::graph::graph_op;
use crate::states::state::EngineState;

/// Execute a ForOp: resolve each/broadcast → iterate → transpose results.
pub(crate) fn execute_for_op(
    py: Python,
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    let inner = op.inner_graph.as_ref().ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "ForOp '{}' missing inner_graph config",
            op.full_name
        ))
    })?;
    let iter_config = op.iteration_config.as_ref().ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "ForOp '{}' missing iteration_config",
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
                    "ForOp '{}': each variables have different lengths: '{}' has {}, '{}' has {}",
                    op.full_name, each_values[0].0, first_len, var_name, this_len
                )));
            }
        }
        first_len
    };

    // 4. Iterate
    let ctx_prefix = if context.is_empty() {
        String::new()
    } else {
        format!("{}.", context)
    };

    let mut results: Vec<PyObject> = Vec::with_capacity(n);
    let mut success_count: usize = 0;

    for i in 0..n {
        let iter_context = format!("{}[{}]", ctx_prefix, i);

        // Store each[var][i] into (op.full_name, var, iter_context)
        for (var_name, list_val) in &each_values {
            let item = list_val.bind(py).get_item(i)?;
            state.set(
                op.full_name.clone(),
                var_name.clone(),
                iter_context.clone(),
                item.unbind(),
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

        // Run inner graph — catch errors (mirrors for_op.py:88-107)
        let iter_result = graph_op::run_graph(py, inner, state, &iter_context)
            .and_then(|_| graph_op::collect_outputs(py, inner, state, &iter_context));

        match iter_result {
            Ok(output) => {
                results.push(output);
                success_count += 1;
            }
            Err(err) => {
                if iter_config.fail_fast {
                    // Propagate immediately (mirrors Python: raise IterationError)
                    return Err(err);
                }
                // Log and continue (mirrors Python: LOGGER.warning + append error dict)
                let logging = py.import_bound("logging")?;
                let logger = logging.call_method1("getLogger", ("hush.core",))?;
                logger.call_method1(
                    "warning",
                    (format!(
                        "[rush] ForOp '{}' iteration {} failed: {}",
                        op.full_name, i, err
                    ),),
                )?;

                let error_dict = PyDict::new_bound(py);
                error_dict.set_item("error", format!("{}", err))?;
                error_dict.set_item("error_type", "PyErr")?;
                results.push(error_dict.unbind().into());
                // Don't increment success_count
            }
        }
    }

    // 5. Transpose results: [{a:1,b:2}, {a:3,b:4}] → {a:[1,3], b:[2,4]}
    let output_keys: Vec<&str> = op
        .outputs
        .iter()
        .filter(|p| p.var_name != "iteration_metrics")
        .map(|p| p.var_name.as_str())
        .collect();

    for key in &output_keys {
        let list = PyList::empty_bound(py);
        for r in &results {
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

    // 6. Add iteration_metrics
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

    // 7. Push output refs
    base::push_output_refs(py, op, state, context)?;

    Ok(())
}
