//! Ref operation interpreter — evaluates serialized Ref._ops chains in pure Rust.
//!
//! Operates on `serde_json::Value` — no Python/GIL needed.
//! Supports: getitem, getattr, arithmetic, comparisons, boolean (and_/or_/rand_/ror_/not_),
//! neg. Nested Ref args are resolved via EngineState lookup.

use std::sync::Arc;

use serde_json::Value;

use crate::config::{RefArg, RefConfig, RefOp};
use crate::error::RushError;
use crate::states::state::EngineState;

// =============================================================================
// JSON truthiness + comparison helpers
// =============================================================================

/// JSON truthiness — mirrors Python's truthiness for JSON values.
///
/// Falsy: null, false, 0, 0.0, "", [], {}
/// Truthy: everything else
pub fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i != 0
            } else if let Some(u) = n.as_u64() {
                u != 0
            } else if let Some(f) = n.as_f64() {
                f != 0.0
            } else {
                true
            }
        }
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Compare two JSON values with the given operator.
pub fn compare_values(lhs: &Value, rhs: &Value, op: &str) -> bool {
    match (lhs, rhs) {
        (Value::Number(a), Value::Number(b)) => {
            let a = a.as_f64().unwrap_or(0.0);
            let b = b.as_f64().unwrap_or(0.0);
            match op {
                "==" => a == b,
                "!=" => a != b,
                ">" => a > b,
                ">=" => a >= b,
                "<" => a < b,
                "<=" => a <= b,
                _ => false,
            }
        }
        (Value::String(a), Value::String(b)) => match op {
            "==" => a == b,
            "!=" => a != b,
            ">" => a > b,
            ">=" => a >= b,
            "<" => a < b,
            "<=" => a <= b,
            _ => false,
        },
        (Value::Bool(a), Value::Bool(b)) => match op {
            "==" => a == b,
            "!=" => a != b,
            _ => false,
        },
        (Value::Null, Value::Null) => op == "==",
        (Value::Null, _) | (_, Value::Null) => op == "!=",
        _ => match op {
            "==" => false,
            "!=" => true,
            _ => false,
        },
    }
}

// =============================================================================
// Ref ops evaluation
// =============================================================================

/// Evaluate a chain of ref operations on a source value.
///
/// State and context are needed for resolving nested Ref arguments.
pub fn evaluate_ref_ops(
    value: Value,
    ops: &[RefOp],
    state: &EngineState,
    context: &str,
) -> Result<Value, RushError> {
    let mut result = value;

    for op in ops {
        result = match op.name.as_str() {
            "getitem" => {
                let key = resolve_arg(&op.args[0], state, context)?;
                match (&result, &key) {
                    (Value::Object(map), Value::String(k)) => {
                        map.get(k).cloned().unwrap_or(Value::Null)
                    }
                    (Value::Array(arr), Value::Number(n)) => {
                        if let Some(idx) = n.as_u64() {
                            arr.get(idx as usize).cloned().unwrap_or(Value::Null)
                        } else if let Some(idx) = n.as_i64() {
                            // Support negative indexing
                            if idx < 0 {
                                let pos = (arr.len() as i64 + idx) as usize;
                                arr.get(pos).cloned().unwrap_or(Value::Null)
                            } else {
                                arr.get(idx as usize).cloned().unwrap_or(Value::Null)
                            }
                        } else {
                            Value::Null
                        }
                    }
                    // String key on array → try parse as int
                    (Value::Array(arr), Value::String(s)) => {
                        if let Ok(idx) = s.parse::<usize>() {
                            arr.get(idx).cloned().unwrap_or(Value::Null)
                        } else {
                            Value::Null
                        }
                    }
                    _ => {
                        return Err(RushError::RefError(format!(
                            "Cannot getitem on {:?} with key {:?}",
                            value_type_name(&result),
                            key
                        )));
                    }
                }
            }
            "getattr" => {
                // In JSON, getattr is equivalent to getitem on objects
                let attr = resolve_arg_as_string(&op.args[0], state, context)?;
                match &result {
                    Value::Object(map) => map.get(&attr).cloned().unwrap_or(Value::Null),
                    _ => {
                        return Err(RushError::RefError(format!(
                            "Cannot getattr '{}' on non-object: {}",
                            attr,
                            value_type_name(&result)
                        )));
                    }
                }
            }
            // Arithmetic ops
            "add" => {
                let other = resolve_arg(&op.args[0], state, context)?;
                // String concatenation
                if let (Value::String(a), Value::String(b)) = (&result, &other) {
                    Value::String(format!("{}{}", a, b))
                } else {
                    json_arithmetic(&result, &other, "add")?
                }
            }
            "sub" => {
                let other = resolve_arg(&op.args[0], state, context)?;
                json_arithmetic(&result, &other, "sub")?
            }
            "mul" => {
                let other = resolve_arg(&op.args[0], state, context)?;
                json_arithmetic(&result, &other, "mul")?
            }
            "truediv" => {
                let other = resolve_arg(&op.args[0], state, context)?;
                json_arithmetic(&result, &other, "truediv")?
            }
            // Comparison ops
            "eq" | "ne" | "lt" | "le" | "gt" | "ge" => {
                let other = resolve_arg(&op.args[0], state, context)?;
                let op_str = match op.name.as_str() {
                    "eq" => "==",
                    "ne" => "!=",
                    "lt" => "<",
                    "le" => "<=",
                    "gt" => ">",
                    "ge" => ">=",
                    _ => unreachable!(),
                };
                Value::Bool(compare_values(&result, &other, op_str))
            }
            // Boolean ops — short-circuit semantics (mirrors ref.py)
            "and_" => {
                if !is_truthy(&result) {
                    result
                } else {
                    resolve_arg(&op.args[0], state, context)?
                }
            }
            "or_" => {
                if is_truthy(&result) {
                    result
                } else {
                    resolve_arg(&op.args[0], state, context)?
                }
            }
            "rand_" => {
                let right = resolve_arg(&op.args[0], state, context)?;
                if !is_truthy(&right) {
                    right
                } else {
                    result
                }
            }
            "ror_" => {
                let right = resolve_arg(&op.args[0], state, context)?;
                if is_truthy(&right) {
                    right
                } else {
                    result
                }
            }
            "not_" => Value::Bool(!is_truthy(&result)),
            "neg" => {
                let n = result.as_f64().ok_or_else(|| {
                    RushError::RefError(format!(
                        "Cannot negate non-number: {}",
                        value_type_name(&result)
                    ))
                })?;
                serde_json::json!(-n)
            }
            "apply" => {
                return Err(RushError::UnsupportedOp(
                    "Ref.apply() is not supported in rust mode. Use @op(rust=...) instead.".into(),
                ));
            }
            unknown => {
                return Err(RushError::RefError(format!("Unknown ref op: {unknown}")));
            }
        };
    }

    Ok(result)
}

// =============================================================================
// Helpers
// =============================================================================

/// Resolve a RefArg to a Value, with state lookup for nested refs.
fn resolve_arg(
    arg: &RefArg,
    state: &EngineState,
    context: &str,
) -> Result<Value, RushError> {
    match arg {
        RefArg::Literal(val) => Ok(val.clone()),
        RefArg::NestedRef(ref_config) => resolve_nested_ref(ref_config, state, context),
    }
}

/// Resolve a nested ref config: look up value in state and apply ops.
fn resolve_nested_ref(
    ref_config: &RefConfig,
    state: &EngineState,
    context: &str,
) -> Result<Value, RushError> {
    let value = state
        .get(&ref_config.source, &ref_config.var, context)
        .ok_or_else(|| {
            RushError::RefError(format!(
                "Nested ref {}.{} not found in state",
                ref_config.source, ref_config.var
            ))
        })?;
    let value = Arc::try_unwrap(value).unwrap_or_else(|arc| (*arc).clone());
    if ref_config.ops.is_empty() {
        Ok(value)
    } else {
        evaluate_ref_ops(value, &ref_config.ops, state, context)
    }
}

/// Resolve a RefArg as a String (for getattr).
fn resolve_arg_as_string(
    arg: &RefArg,
    state: &EngineState,
    context: &str,
) -> Result<String, RushError> {
    let val = resolve_arg(arg, state, context)?;
    val.as_str()
        .map(|s| s.to_string())
        .ok_or_else(|| RushError::RefError(format!("Expected string for getattr, got: {:?}", val)))
}

/// Arithmetic on serde_json::Value.
fn json_arithmetic(lhs: &Value, rhs: &Value, op: &str) -> Result<Value, RushError> {
    let a = lhs
        .as_f64()
        .ok_or_else(|| RushError::RefError(format!("Cannot perform {op} on non-number: {lhs}")))?;
    let b = rhs
        .as_f64()
        .ok_or_else(|| RushError::RefError(format!("Cannot perform {op} on non-number: {rhs}")))?;

    let result = match op {
        "add" => a + b,
        "sub" => a - b,
        "mul" => a * b,
        "truediv" => {
            if b == 0.0 {
                return Err(RushError::RefError("Division by zero".into()));
            }
            a / b
        }
        _ => return Err(RushError::RefError(format!("Unknown arithmetic op: {op}"))),
    };

    // Preserve integer type if both inputs were integers and result is integer
    if lhs.is_i64() && rhs.is_i64() && op != "truediv" && (result as i64 as f64) == result {
        return Ok(Value::Number(serde_json::Number::from(result as i64)));
    }
    Ok(serde_json::json!(result))
}

/// Human-readable type name for error messages.
fn value_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "null",
        Value::Bool(_) => "bool",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}
