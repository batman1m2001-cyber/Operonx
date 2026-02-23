//! Shared helpers for iteration ops (ForOp, MapOp, AIterOp).
//!
//! Extracts common patterns: resolving each/broadcast params, validating lengths,
//! storing empty results, and building iteration metrics.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::config::{IterationConfig, OpConfig};
use crate::ops::base;
use crate::states::state::EngineState;

/// Resolve `each` iteration parameters to `(var_name, list_value)` pairs.
pub(crate) fn resolve_each_values(
    py: Python,
    iter_config: &IterationConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<Vec<(String, PyObject)>> {
    let mut each_values = Vec::new();
    for param in &iter_config.each {
        if let Some(value) = base::resolve_iter_param(py, param, state, context)? {
            each_values.push((param.var_name.clone(), value));
        }
    }
    Ok(each_values)
}

/// Resolve `broadcast` iteration parameters to `(var_name, value)` pairs.
pub(crate) fn resolve_broadcast_values(
    py: Python,
    iter_config: &IterationConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<Vec<(String, PyObject)>> {
    let mut broadcast_values = Vec::new();
    for param in &iter_config.broadcast {
        if let Some(value) = base::resolve_iter_param(py, param, state, context)? {
            broadcast_values.push((param.var_name.clone(), value));
        }
    }
    Ok(broadcast_values)
}

/// Determine iteration count from `each` values and validate equal lengths.
///
/// Returns 0 if both each and broadcast are empty, 1 if only broadcast, or the
/// validated list length if each values are present.
pub(crate) fn determine_iteration_count(
    py: Python,
    op_name: &str,
    each_values: &[(String, PyObject)],
    broadcast_values: &[(String, PyObject)],
) -> PyResult<usize> {
    if each_values.is_empty() {
        return Ok(if broadcast_values.is_empty() { 0 } else { 1 });
    }

    let first_len = each_values[0].1.bind(py).len()?;
    for (var_name, val) in &each_values[1..] {
        let this_len = val.bind(py).len()?;
        if this_len != first_len {
            return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "{}: each variables have different lengths: '{}' has {}, '{}' has {}",
                op_name, each_values[0].0, first_len, var_name, this_len
            )));
        }
    }
    Ok(first_len)
}

/// Store empty results for zero-iteration case.
///
/// Sets all output keys (except iteration_metrics) to empty lists,
/// adds zeroed iteration_metrics, and pushes output refs.
pub(crate) fn store_empty_results(
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

    store_iteration_metrics(py, op, state, context, 0, 0, 0)?;
    base::push_output_refs(py, op, state, context)?;

    Ok(())
}

/// Build and store iteration_metrics dict in state.
pub(crate) fn store_iteration_metrics(
    py: Python,
    op: &OpConfig,
    state: &EngineState,
    context: &str,
    total: usize,
    success: usize,
    error: usize,
) -> PyResult<()> {
    let metrics = PyDict::new_bound(py);
    metrics.set_item("total_iterations", total)?;
    metrics.set_item("success_count", success)?;
    metrics.set_item("error_count", error)?;
    state.set(
        op.full_name.clone(),
        "iteration_metrics".to_string(),
        context.to_string(),
        metrics.unbind().into(),
    );
    Ok(())
}

/// Build the iteration context prefix string.
pub(crate) fn context_prefix(context: &str) -> String {
    if context.is_empty() {
        String::new()
    } else {
        format!("{}.", context)
    }
}
