//! Ref operation interpreter — evaluates serialized Ref._ops chains in Rust.
//!
//! Mirrors what Python's `Ref._fn` lambdas do, using PyO3's `PyAny` operations.
//! All operations go through PyO3 so any Python object type is supported.
//!
//! Supports: getitem, getattr, arithmetic, comparisons, boolean (and_/or_/rand_/ror_/not_),
//! apply, neg. Nested Ref args are resolved via EngineState lookup.

use pyo3::prelude::*;
use pyo3::conversion::ToPyObject;

use crate::config::{RefArg, RefOp};
use crate::states::state::EngineState;

/// Evaluate a chain of ref operations on a source value.
///
/// State and context are needed for resolving nested Ref arguments
/// (e.g. `(PARENT["a"] > 10) & (PARENT["b"] == "x")`).
pub fn evaluate_ref_ops(
    py: Python,
    value: PyObject,
    ops: &[RefOp],
    state: &EngineState,
    context: &str,
) -> PyResult<PyObject> {
    let mut result = value;

    for op in ops {
        result = match op.name.as_str() {
            "getitem" => {
                let key = resolve_arg(py, &op.args[0], state, context)?;
                result
                    .bind(py)
                    .get_item(key.bind(py))?
                    .unbind()
            }
            "getattr" => {
                let attr: String = resolve_arg_as_string(py, &op.args[0], state, context)?;
                result
                    .bind(py)
                    .getattr(attr.as_str())?
                    .unbind()
            }
            // Arithmetic ops
            "add" => {
                let other = resolve_arg(py, &op.args[0], state, context)?;
                result.bind(py).call_method1("__add__", (other,))?.unbind()
            }
            "sub" => {
                let other = resolve_arg(py, &op.args[0], state, context)?;
                result.bind(py).call_method1("__sub__", (other,))?.unbind()
            }
            "mul" => {
                let other = resolve_arg(py, &op.args[0], state, context)?;
                result.bind(py).call_method1("__mul__", (other,))?.unbind()
            }
            "truediv" => {
                let other = resolve_arg(py, &op.args[0], state, context)?;
                result.bind(py).call_method1("__truediv__", (other,))?.unbind()
            }
            // Comparison ops
            "eq" => {
                let other = resolve_arg(py, &op.args[0], state, context)?;
                result.bind(py).rich_compare(other.bind(py), pyo3::basic::CompareOp::Eq)?.unbind()
            }
            "ne" => {
                let other = resolve_arg(py, &op.args[0], state, context)?;
                result.bind(py).rich_compare(other.bind(py), pyo3::basic::CompareOp::Ne)?.unbind()
            }
            "lt" => {
                let other = resolve_arg(py, &op.args[0], state, context)?;
                result.bind(py).rich_compare(other.bind(py), pyo3::basic::CompareOp::Lt)?.unbind()
            }
            "le" => {
                let other = resolve_arg(py, &op.args[0], state, context)?;
                result.bind(py).rich_compare(other.bind(py), pyo3::basic::CompareOp::Le)?.unbind()
            }
            "gt" => {
                let other = resolve_arg(py, &op.args[0], state, context)?;
                result.bind(py).rich_compare(other.bind(py), pyo3::basic::CompareOp::Gt)?.unbind()
            }
            "ge" => {
                let other = resolve_arg(py, &op.args[0], state, context)?;
                result.bind(py).rich_compare(other.bind(py), pyo3::basic::CompareOp::Ge)?.unbind()
            }
            // Boolean ops — short-circuit semantics (mirrors ref.py:199-217)
            "and_" => {
                // left and right: if left is falsy, return left; else evaluate right
                if !result.bind(py).is_truthy()? {
                    result
                } else {
                    resolve_arg(py, &op.args[0], state, context)?
                }
            }
            "or_" => {
                // left or right: if left is truthy, return left; else evaluate right
                if result.bind(py).is_truthy()? {
                    result
                } else {
                    resolve_arg(py, &op.args[0], state, context)?
                }
            }
            "rand_" => {
                // reverse and: right and left
                let right = resolve_arg(py, &op.args[0], state, context)?;
                if !right.bind(py).is_truthy()? {
                    right
                } else {
                    result
                }
            }
            "ror_" => {
                // reverse or: right or left
                let right = resolve_arg(py, &op.args[0], state, context)?;
                if right.bind(py).is_truthy()? {
                    right
                } else {
                    result
                }
            }
            "not_" => {
                let v = result.bind(py).is_truthy()?;
                let b = !v;
                PyObject::from(b.to_object(py))
            }
            // Apply callable
            "apply" => {
                let callable = resolve_arg(py, &op.args[0], state, context)?;
                callable.call1(py, (result,))?
            }
            "neg" => {
                result.bind(py).call_method0("__neg__")?.unbind()
            }
            unknown => {
                return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(
                    format!("Unknown ref op: {unknown}"),
                ));
            }
        };
    }

    Ok(result)
}

/// Resolve a RefArg to a PyObject, with state lookup for nested refs.
fn resolve_arg(
    py: Python,
    arg: &RefArg,
    state: &EngineState,
    context: &str,
) -> PyResult<PyObject> {
    match arg {
        RefArg::Literal(obj) => Ok(obj.clone_ref(py)),
        RefArg::Callable(obj) => Ok(obj.clone_ref(py)),
        RefArg::NestedRef(ref_config) => {
            // Look up the nested ref's value in state
            let value = state
                .get(py, &ref_config.source, &ref_config.var, context)
                .ok_or_else(|| {
                    PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                        "Nested ref {}.{} not found in state",
                        ref_config.source, ref_config.var
                    ))
                })?;
            // If the nested ref has ops, evaluate them recursively
            if ref_config.ops.is_empty() {
                Ok(value.clone_ref(py))
            } else {
                evaluate_ref_ops(py, value.clone_ref(py), &ref_config.ops, state, context)
            }
        }
    }
}

/// Resolve a RefArg as a String (for getattr).
fn resolve_arg_as_string(
    py: Python,
    arg: &RefArg,
    state: &EngineState,
    context: &str,
) -> PyResult<String> {
    let obj = resolve_arg(py, arg, state, context)?;
    obj.extract::<String>(py)
}
