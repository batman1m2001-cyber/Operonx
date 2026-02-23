//! ForOp execution — iterate over lists with broadcast support.
//!
//! Mirrors Python's `ops/iteration/for_op.py` (ForOp).

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::config::OpConfig;
use crate::ops::base;
use crate::ops::graph::graph_op;
use crate::ops::iteration::helpers;
use crate::states::state::EngineState;

/// Execute a ForOp: resolve each/broadcast → iterate → transpose results.
pub(crate) fn run(
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

    // 2. Resolve each/broadcast values and determine iteration count
    let each_values = helpers::resolve_each_values(py, iter_config, state, context)?;
    let broadcast_values = helpers::resolve_broadcast_values(py, iter_config, state, context)?;
    let n = helpers::determine_iteration_count(py, &op.full_name, &each_values, &broadcast_values)?;

    // 3. Iterate
    let ctx_prefix = helpers::context_prefix(context);
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
            .and_then(|_| graph_op::get_outputs(py, inner, state, &iter_context));

        match iter_result {
            Ok(output) => {
                results.push(output);
                success_count += 1;
            }
            Err(err) => {
                if iter_config.fail_fast {
                    return Err(err);
                }
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
            }
        }
    }

    // 4. Transpose results and store
    super::map_op::transpose_and_store(py, op, &results, state, context)?;

    // 5. Add iteration_metrics and push output refs
    helpers::store_iteration_metrics(py, op, state, context, n, success_count, n - success_count)?;
    base::push_output_refs(py, op, state, context)?;

    Ok(())
}
