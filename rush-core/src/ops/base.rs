//! Base op execution — leaf ops, ref resolution, result storage.
//!
//! Mirrors Python's `ops/base.py` (BaseOp.run, store_result, resolve).
//! Includes observability: enabled flag, per-op timing, $tags, verbose logging,
//! slow op warnings, and execution order tracking.

use std::time::Instant;

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::config::{IterParamConfig, OpConfig, ParamConfig, RefConfig};
use crate::ops::transform::func_op;
use crate::refs::interpreter::evaluate_ref_ops;
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

        // 4. Execute: try Rust registry first, then Python callable
        let result_obj = if let Some(ref rust_name) = op.rust_op {
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
pub(crate) fn call_python(
    py: Python,
    op: &OpConfig,
    inputs_dict: &Bound<'_, PyDict>,
) -> PyResult<Option<PyObject>> {
    match &op.python_callable {
        Some(callable) => {
            let result = callable.bind(py).call((), Some(inputs_dict))?;
            Ok(Some(result.unbind()))
        }
        None => Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "Op '{}' has no python_callable and no rust_op",
            op.full_name
        ))),
    }
}
