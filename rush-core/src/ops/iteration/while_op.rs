//! WhileOp execution — loop until condition or max iterations.
//!
//! Mirrors Python's `ops/iteration/while_op.py` (WhileOp).

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::config::OpConfig;
use crate::ops::base;
use crate::ops::graph::graph_op;
use crate::states::state::EngineState;

/// Execute a WhileOp: loop until condition is True or max_iterations reached.
pub(crate) fn execute_while_op(
    py: Python,
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    let inner = op.inner_graph.as_ref().ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "WhileOp '{}' missing inner_graph config",
            op.full_name
        ))
    })?;
    let iter_config = op.iteration_config.as_ref().ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "WhileOp '{}' missing iteration_config",
            op.full_name
        ))
    })?;

    let max_iterations = iter_config.max_iterations.unwrap_or(100);
    let until_expr = iter_config.until.as_deref();

    // 1. Resolve initial inputs into step_inputs dict
    let step_inputs = PyDict::new_bound(py);
    for param in &op.inputs {
        if let Some(value) = base::resolve_param(py, param, state, context)? {
            step_inputs.set_item(&param.var_name, value.bind(py))?;
        }
    }

    // Also resolve broadcast values
    for param in &iter_config.broadcast {
        if let Some(value) = base::resolve_iter_param(py, param, state, context)? {
            step_inputs.set_item(&param.var_name, value.bind(py))?;
        }
    }

    // 2. Evaluate initial condition (catch errors — mirrors while_op.py:70-96)
    let mut should_stop = match until_expr {
        Some(expr) => match evaluate_until(py, expr, &step_inputs) {
            Ok(val) => val,
            Err(err) => {
                let logging = py.import_bound("logging")?;
                let logger = logging.call_method1("getLogger", ("hush.core",))?;
                logger.call_method1(
                    "warning",
                    (format!(
                        "[rush] WhileOp '{}' condition error: {}",
                        op.full_name, err
                    ),),
                )?;
                false // Continue loop on condition error (mirrors Python)
            }
        },
        None => false,
    };

    let ctx_prefix = if context.is_empty() {
        String::new()
    } else {
        format!("{}.", context)
    };
    let mut step_count: usize = 0;

    // 3. Loop
    while !should_stop && step_count < max_iterations {
        let step_context = format!("{}[{}]", ctx_prefix, step_count);

        // Store step_inputs into state
        for (k, v) in step_inputs.iter() {
            let key: String = k.extract()?;
            state.set(
                op.full_name.clone(),
                key,
                step_context.clone(),
                v.unbind(),
            );
        }

        // Run inner graph
        graph_op::run_graph(py, inner, state, &step_context)?;

        // Collect outputs and merge into step_inputs
        let outputs = graph_op::collect_outputs(py, inner, state, &step_context)?;
        if let Ok(outputs_dict) = outputs.downcast_bound::<PyDict>(py) {
            for (k, v) in outputs_dict.iter() {
                step_inputs.set_item(k, v)?;
            }
        }

        step_count += 1;

        // Re-evaluate condition (catch errors — mirrors while_op.py:70-96)
        should_stop = match until_expr {
            Some(expr) => match evaluate_until(py, expr, &step_inputs) {
                Ok(val) => val,
                Err(err) => {
                    let logging = py.import_bound("logging")?;
                    let logger = logging.call_method1("getLogger", ("hush.core",))?;
                    logger.call_method1(
                        "warning",
                        (format!(
                            "[rush] WhileOp '{}' condition error: {}",
                            op.full_name, err
                        ),),
                    )?;
                    false // Continue loop on condition error (mirrors Python)
                }
            },
            None => false,
        };
    }

    // 4. Store final step_inputs as outputs in parent context
    for (k, v) in step_inputs.iter() {
        let key: String = k.extract()?;
        state.set(
            op.full_name.clone(),
            key,
            context.to_string(),
            v.unbind(),
        );
    }

    // 5. Add iteration_metrics
    let metrics = PyDict::new_bound(py);
    metrics.set_item("total_iterations", step_count)?;
    metrics.set_item("success_count", step_count)?;
    metrics.set_item("error_count", 0)?;
    metrics.set_item("max_iterations_reached", step_count >= max_iterations)?;
    metrics.set_item("stopped_by_condition", should_stop)?;
    state.set(
        op.full_name.clone(),
        "iteration_metrics".to_string(),
        context.to_string(),
        metrics.unbind().into(),
    );

    // 6. Push output refs
    base::push_output_refs(py, op, state, context)?;

    Ok(())
}

/// Evaluate a WhileOp's `until` expression against current inputs.
pub(crate) fn evaluate_until(
    py: Python,
    expr: &str,
    inputs: &Bound<'_, PyDict>,
) -> PyResult<bool> {
    let globals = PyDict::new_bound(py);
    globals.set_item("__builtins__", PyDict::new_bound(py))?;
    let result = py.eval_bound(expr, Some(&globals), Some(inputs))?;
    result.extract::<bool>()
}
