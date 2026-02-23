//! WhileOp execution — loop until condition or max iterations.
//!
//! Mirrors Python's `ops/iteration/while_op.py` (WhileOp).

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::config::OpConfig;
use crate::ops::base;
use crate::ops::graph::graph_op;
use crate::ops::iteration::helpers;
use crate::states::state::EngineState;

/// Execute a WhileOp: loop until condition is True or max_iterations reached.
pub(crate) fn run(
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

    // 2. Evaluate initial condition
    let mut should_stop = evaluate_condition(py, &op.full_name, until_expr, &step_inputs)?;

    let ctx_prefix = helpers::context_prefix(context);
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
        let outputs = graph_op::get_outputs(py, inner, state, &step_context)?;
        if let Ok(outputs_dict) = outputs.downcast_bound::<PyDict>(py) {
            for (k, v) in outputs_dict.iter() {
                step_inputs.set_item(k, v)?;
            }
        }

        step_count += 1;

        // Re-evaluate condition
        should_stop = evaluate_condition(py, &op.full_name, until_expr, &step_inputs)?;
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

/// Evaluate the `until` condition, catching errors and logging warnings.
///
/// Returns `false` on error (continue loop), matching Python's behavior.
fn evaluate_condition(
    py: Python,
    op_name: &str,
    until_expr: Option<&str>,
    inputs: &Bound<'_, PyDict>,
) -> PyResult<bool> {
    let expr = match until_expr {
        Some(e) => e,
        None => return Ok(false),
    };

    match evaluate_until(py, expr, inputs) {
        Ok(val) => Ok(val),
        Err(err) => {
            let logging = py.import_bound("logging")?;
            let logger = logging.call_method1("getLogger", ("hush.core",))?;
            logger.call_method1(
                "warning",
                (format!(
                    "[rush] WhileOp '{}' condition error: {}",
                    op_name, err
                ),),
            )?;
            Ok(false)
        }
    }
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
