//! Ref transform evaluator — evaluates serialized Ref._transforms chains in pure Rust.
//!
//! Operates on `serde_json::Value` — no Python/GIL needed.
//! Full parity with Python's Ref._wrap() transforms:
//!   Access: getitem, getattr, call
//!   Arithmetic: add/radd, sub/rsub, mul/rmul, truediv/rtruediv, floordiv/rfloordiv,
//!              mod/rmod, pow/rpow, matmul/rmatmul
//!   Unary: neg, pos, abs
//!   Comparison: eq, ne, lt, le, gt, ge, contains
//!   Boolean: and_, or_, rand_, ror_, not_
//!   Custom: apply (unsupported — errors with guidance)
//!
//! Nested Ref args are resolved via EngineState lookup.

use std::sync::Arc;

use serde_json::Value;

use crate::config::{RefArg, RefConfig, RefTransform};
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

/// Evaluate a chain of ref transforms on a source value.
///
/// State and context are needed for resolving nested Ref arguments.
pub fn evaluate_ref_transforms(
    value: Value,
    transforms: &[RefTransform],
    state: &EngineState,
    context: &str,
) -> Result<Value, RushError> {
    let mut result = value;

    for t in transforms {
        result = match t.name.as_str() {
            // =================================================================
            // Access
            // =================================================================
            "getitem" => {
                let key = resolve_arg(&t.args[0], state, context)?;
                match (&result, &key) {
                    (Value::Object(map), Value::String(k)) => {
                        map.get(k).cloned().unwrap_or(Value::Null)
                    }
                    (Value::Array(arr), Value::Number(n)) => {
                        if let Some(idx) = n.as_u64() {
                            arr.get(idx as usize).cloned().unwrap_or(Value::Null)
                        } else if let Some(idx) = n.as_i64() {
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
                let attr = resolve_arg_as_string(&t.args[0], state, context)?;
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
            "call" => {
                return Err(RushError::UnsupportedOp(
                    "Ref.__call__() is not supported in Rust mode. Use @op(rust=...) instead."
                        .into(),
                ));
            }

            // =================================================================
            // Arithmetic: forward (result OP other)
            // =================================================================
            "add" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                // String + String → concatenation
                if let (Value::String(a), Value::String(b)) = (&result, &other) {
                    Value::String(format!("{}{}", a, b))
                // Array + Array → concatenation
                } else if let (Value::Array(a), Value::Array(b)) = (&result, &other) {
                    let mut merged = a.clone();
                    merged.extend(b.iter().cloned());
                    Value::Array(merged)
                } else {
                    json_arithmetic(&result, &other, "add")?
                }
            }
            "sub" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                json_arithmetic(&result, &other, "sub")?
            }
            "mul" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                // String * int → repeat
                if let (Value::String(s), Value::Number(n)) = (&result, &other) {
                    if let Some(count) = n.as_u64() {
                        Value::String(s.repeat(count as usize))
                    } else {
                        json_arithmetic(&result, &other, "mul")?
                    }
                } else {
                    json_arithmetic(&result, &other, "mul")?
                }
            }
            "truediv" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                json_arithmetic(&result, &other, "truediv")?
            }
            "floordiv" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                json_arithmetic(&result, &other, "floordiv")?
            }
            "mod" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                json_arithmetic(&result, &other, "mod")?
            }
            "pow" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                json_arithmetic(&result, &other, "pow")?
            }
            "matmul" => {
                return Err(RushError::UnsupportedOp(
                    "Ref.__matmul__() (@) is not supported in Rust mode.".into(),
                ));
            }

            // =================================================================
            // Arithmetic: reverse (other OP result)
            // =================================================================
            "radd" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                if let (Value::String(a), Value::String(b)) = (&other, &result) {
                    Value::String(format!("{}{}", a, b))
                } else if let (Value::Array(a), Value::Array(b)) = (&other, &result) {
                    let mut merged = a.clone();
                    merged.extend(b.iter().cloned());
                    Value::Array(merged)
                } else {
                    json_arithmetic(&other, &result, "add")?
                }
            }
            "rsub" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                json_arithmetic(&other, &result, "sub")?
            }
            "rmul" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                if let (Value::Number(n), Value::String(s)) = (&other, &result) {
                    if let Some(count) = n.as_u64() {
                        Value::String(s.repeat(count as usize))
                    } else {
                        json_arithmetic(&other, &result, "mul")?
                    }
                } else {
                    json_arithmetic(&other, &result, "mul")?
                }
            }
            "rtruediv" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                json_arithmetic(&other, &result, "truediv")?
            }
            "rfloordiv" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                json_arithmetic(&other, &result, "floordiv")?
            }
            "rmod" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                json_arithmetic(&other, &result, "mod")?
            }
            "rpow" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                json_arithmetic(&other, &result, "pow")?
            }
            "rmatmul" => {
                return Err(RushError::UnsupportedOp(
                    "Ref.__rmatmul__() (@) is not supported in Rust mode.".into(),
                ));
            }

            // =================================================================
            // Unary
            // =================================================================
            "neg" => {
                let n = as_f64(&result, "neg")?;
                to_json_number(-n, result.is_i64())
            }
            "pos" => {
                let n = as_f64(&result, "pos")?;
                to_json_number(n, result.is_i64())
            }
            "abs" => {
                let n = as_f64(&result, "abs")?;
                to_json_number(n.abs(), result.is_i64())
            }

            // =================================================================
            // Comparison
            // =================================================================
            "eq" | "ne" | "lt" | "le" | "gt" | "ge" => {
                let other = resolve_arg(&t.args[0], state, context)?;
                let op_str = match t.name.as_str() {
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
            "contains" => {
                let item = resolve_arg(&t.args[0], state, context)?;
                match &result {
                    Value::Array(arr) => Value::Bool(arr.contains(&item)),
                    Value::Object(map) => {
                        if let Value::String(k) = &item {
                            Value::Bool(map.contains_key(k.as_str()))
                        } else {
                            Value::Bool(false)
                        }
                    }
                    Value::String(s) => {
                        if let Value::String(sub) = &item {
                            Value::Bool(s.contains(sub.as_str()))
                        } else {
                            Value::Bool(false)
                        }
                    }
                    _ => Value::Bool(false),
                }
            }

            // =================================================================
            // Boolean — short-circuit semantics (mirrors ref.py)
            // =================================================================
            "and_" => {
                if !is_truthy(&result) {
                    result
                } else {
                    resolve_arg(&t.args[0], state, context)?
                }
            }
            "or_" => {
                if is_truthy(&result) {
                    result
                } else {
                    resolve_arg(&t.args[0], state, context)?
                }
            }
            "rand_" => {
                let right = resolve_arg(&t.args[0], state, context)?;
                if !is_truthy(&right) {
                    right
                } else {
                    result
                }
            }
            "ror_" => {
                let right = resolve_arg(&t.args[0], state, context)?;
                if is_truthy(&right) {
                    right
                } else {
                    result
                }
            }
            "not_" => Value::Bool(!is_truthy(&result)),

            // =================================================================
            // Custom function — not possible in pure Rust
            // =================================================================
            "apply" => {
                return Err(RushError::UnsupportedOp(
                    "Ref.apply() is not supported in Rust mode. Use @op(rust=...) instead.".into(),
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

/// Resolve a nested ref config: look up value in state and apply transforms.
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
    if ref_config.transforms.is_empty() {
        Ok(value)
    } else {
        evaluate_ref_transforms(value, &ref_config.transforms, state, context)
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

/// Extract f64 from a JSON value, with op name for error context.
fn as_f64(v: &Value, op: &str) -> Result<f64, RushError> {
    v.as_f64()
        .ok_or_else(|| RushError::RefError(format!("Cannot {op} non-number: {}", value_type_name(v))))
}

/// Convert f64 back to JSON, preserving integer type when possible.
fn to_json_number(n: f64, prefer_int: bool) -> Value {
    if prefer_int && (n as i64 as f64) == n {
        Value::Number(serde_json::Number::from(n as i64))
    } else {
        serde_json::json!(n)
    }
}

/// Arithmetic on serde_json::Value.
fn json_arithmetic(lhs: &Value, rhs: &Value, op: &str) -> Result<Value, RushError> {
    let a = as_f64(lhs, op)?;
    let b = as_f64(rhs, op)?;

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
        "floordiv" => {
            if b == 0.0 {
                return Err(RushError::RefError("Floor division by zero".into()));
            }
            (a / b).floor()
        }
        "mod" => {
            if b == 0.0 {
                return Err(RushError::RefError("Modulo by zero".into()));
            }
            // Python-style modulo: result has same sign as divisor
            ((a % b) + b) % b
        }
        "pow" => a.powf(b),
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

// =============================================================================
// Unit tests
// =============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn empty_state() -> EngineState {
        EngineState::new()
    }

    fn make_transform(name: &str, args: Vec<RefArg>) -> RefTransform {
        RefTransform {
            name: name.to_string(),
            args,
        }
    }

    fn lit(v: Value) -> RefArg {
        RefArg::Literal(v)
    }

    // ----- getitem -----

    #[test]
    fn test_getitem_object() {
        let state = empty_state();
        let ops = vec![make_transform("getitem", vec![lit(json!("b"))])];
        let result = evaluate_ref_transforms(json!({"a": 1, "b": 2}), &ops, &state, "").unwrap();
        assert_eq!(result, json!(2));
    }

    #[test]
    fn test_getitem_array_index() {
        let state = empty_state();
        let ops = vec![make_transform("getitem", vec![lit(json!(1))])];
        let result = evaluate_ref_transforms(json!([10, 20, 30]), &ops, &state, "").unwrap();
        assert_eq!(result, json!(20));
    }

    #[test]
    fn test_getitem_array_negative_index() {
        let state = empty_state();
        let ops = vec![make_transform("getitem", vec![lit(json!(-1))])];
        let result = evaluate_ref_transforms(json!([10, 20, 30]), &ops, &state, "").unwrap();
        assert_eq!(result, json!(30));
    }

    // ----- arithmetic forward -----

    #[test]
    fn test_add_numbers() {
        let state = empty_state();
        let ops = vec![make_transform("add", vec![lit(json!(3))])];
        let result = evaluate_ref_transforms(json!(7), &ops, &state, "").unwrap();
        assert_eq!(result, json!(10));
    }

    #[test]
    fn test_add_strings() {
        let state = empty_state();
        let ops = vec![make_transform("add", vec![lit(json!(" world"))])];
        let result = evaluate_ref_transforms(json!("hello"), &ops, &state, "").unwrap();
        assert_eq!(result, json!("hello world"));
    }

    #[test]
    fn test_add_arrays() {
        let state = empty_state();
        let ops = vec![make_transform("add", vec![lit(json!([3, 4]))])];
        let result = evaluate_ref_transforms(json!([1, 2]), &ops, &state, "").unwrap();
        assert_eq!(result, json!([1, 2, 3, 4]));
    }

    #[test]
    fn test_sub() {
        let state = empty_state();
        let ops = vec![make_transform("sub", vec![lit(json!(3))])];
        let result = evaluate_ref_transforms(json!(10), &ops, &state, "").unwrap();
        assert_eq!(result, json!(7));
    }

    #[test]
    fn test_mul() {
        let state = empty_state();
        let ops = vec![make_transform("mul", vec![lit(json!(4))])];
        let result = evaluate_ref_transforms(json!(3), &ops, &state, "").unwrap();
        assert_eq!(result, json!(12));
    }

    #[test]
    fn test_mul_string_repeat() {
        let state = empty_state();
        let ops = vec![make_transform("mul", vec![lit(json!(3))])];
        let result = evaluate_ref_transforms(json!("ab"), &ops, &state, "").unwrap();
        assert_eq!(result, json!("ababab"));
    }

    #[test]
    fn test_truediv() {
        let state = empty_state();
        let ops = vec![make_transform("truediv", vec![lit(json!(4))])];
        let result = evaluate_ref_transforms(json!(10), &ops, &state, "").unwrap();
        assert_eq!(result, json!(2.5));
    }

    #[test]
    fn test_floordiv() {
        let state = empty_state();
        let ops = vec![make_transform("floordiv", vec![lit(json!(3))])];
        let result = evaluate_ref_transforms(json!(10), &ops, &state, "").unwrap();
        assert_eq!(result, json!(3));
    }

    #[test]
    fn test_mod() {
        let state = empty_state();
        let ops = vec![make_transform("mod", vec![lit(json!(3))])];
        let result = evaluate_ref_transforms(json!(10), &ops, &state, "").unwrap();
        assert_eq!(result, json!(1));
    }

    #[test]
    fn test_mod_python_style() {
        // Python: -7 % 3 == 2 (result has sign of divisor)
        let state = empty_state();
        let ops = vec![make_transform("mod", vec![lit(json!(3))])];
        let result = evaluate_ref_transforms(json!(-7), &ops, &state, "").unwrap();
        assert_eq!(result, json!(2));
    }

    #[test]
    fn test_pow() {
        let state = empty_state();
        let ops = vec![make_transform("pow", vec![lit(json!(3))])];
        let result = evaluate_ref_transforms(json!(2), &ops, &state, "").unwrap();
        assert_eq!(result, json!(8));
    }

    // ----- arithmetic reverse -----

    #[test]
    fn test_radd() {
        let state = empty_state();
        // 5 + result(3) = 8
        let ops = vec![make_transform("radd", vec![lit(json!(5))])];
        let result = evaluate_ref_transforms(json!(3), &ops, &state, "").unwrap();
        assert_eq!(result, json!(8));
    }

    #[test]
    fn test_rsub() {
        let state = empty_state();
        // 10 - result(3) = 7
        let ops = vec![make_transform("rsub", vec![lit(json!(10))])];
        let result = evaluate_ref_transforms(json!(3), &ops, &state, "").unwrap();
        assert_eq!(result, json!(7));
    }

    #[test]
    fn test_rmul() {
        let state = empty_state();
        // 4 * result(3) = 12
        let ops = vec![make_transform("rmul", vec![lit(json!(4))])];
        let result = evaluate_ref_transforms(json!(3), &ops, &state, "").unwrap();
        assert_eq!(result, json!(12));
    }

    #[test]
    fn test_rtruediv() {
        let state = empty_state();
        // 10 / result(4) = 2.5
        let ops = vec![make_transform("rtruediv", vec![lit(json!(10))])];
        let result = evaluate_ref_transforms(json!(4), &ops, &state, "").unwrap();
        assert_eq!(result, json!(2.5));
    }

    #[test]
    fn test_rfloordiv() {
        let state = empty_state();
        // 10 // result(3) = 3
        let ops = vec![make_transform("rfloordiv", vec![lit(json!(10))])];
        let result = evaluate_ref_transforms(json!(3), &ops, &state, "").unwrap();
        assert_eq!(result, json!(3));
    }

    #[test]
    fn test_rmod() {
        let state = empty_state();
        // 10 % result(3) = 1
        let ops = vec![make_transform("rmod", vec![lit(json!(10))])];
        let result = evaluate_ref_transforms(json!(3), &ops, &state, "").unwrap();
        assert_eq!(result, json!(1));
    }

    #[test]
    fn test_rpow() {
        let state = empty_state();
        // 2 ** result(3) = 8
        let ops = vec![make_transform("rpow", vec![lit(json!(2))])];
        let result = evaluate_ref_transforms(json!(3), &ops, &state, "").unwrap();
        assert_eq!(result, json!(8));
    }

    // ----- unary -----

    #[test]
    fn test_neg() {
        let state = empty_state();
        let ops = vec![make_transform("neg", vec![])];
        let result = evaluate_ref_transforms(json!(5), &ops, &state, "").unwrap();
        assert_eq!(result, json!(-5));
    }

    #[test]
    fn test_pos() {
        let state = empty_state();
        let ops = vec![make_transform("pos", vec![])];
        let result = evaluate_ref_transforms(json!(-3), &ops, &state, "").unwrap();
        assert_eq!(result, json!(-3));
    }

    #[test]
    fn test_abs() {
        let state = empty_state();
        let ops = vec![make_transform("abs", vec![])];
        let result = evaluate_ref_transforms(json!(-7), &ops, &state, "").unwrap();
        assert_eq!(result, json!(7));
    }

    // ----- comparison -----

    #[test]
    fn test_eq() {
        let state = empty_state();
        let ops = vec![make_transform("eq", vec![lit(json!(5))])];
        assert_eq!(
            evaluate_ref_transforms(json!(5), &ops, &state, "").unwrap(),
            json!(true)
        );
        assert_eq!(
            evaluate_ref_transforms(json!(3), &ops, &state, "").unwrap(),
            json!(false)
        );
    }

    #[test]
    fn test_gt_lt() {
        let state = empty_state();
        let ops = vec![make_transform("gt", vec![lit(json!(3))])];
        assert_eq!(
            evaluate_ref_transforms(json!(5), &ops, &state, "").unwrap(),
            json!(true)
        );
        let ops = vec![make_transform("lt", vec![lit(json!(3))])];
        assert_eq!(
            evaluate_ref_transforms(json!(5), &ops, &state, "").unwrap(),
            json!(false)
        );
    }

    #[test]
    fn test_contains_array() {
        let state = empty_state();
        let ops = vec![make_transform("contains", vec![lit(json!(2))])];
        assert_eq!(
            evaluate_ref_transforms(json!([1, 2, 3]), &ops, &state, "").unwrap(),
            json!(true)
        );
        let ops = vec![make_transform("contains", vec![lit(json!(9))])];
        assert_eq!(
            evaluate_ref_transforms(json!([1, 2, 3]), &ops, &state, "").unwrap(),
            json!(false)
        );
    }

    #[test]
    fn test_contains_string() {
        let state = empty_state();
        let ops = vec![make_transform("contains", vec![lit(json!("ell"))])];
        assert_eq!(
            evaluate_ref_transforms(json!("hello"), &ops, &state, "").unwrap(),
            json!(true)
        );
    }

    #[test]
    fn test_contains_object_key() {
        let state = empty_state();
        let ops = vec![make_transform("contains", vec![lit(json!("a"))])];
        assert_eq!(
            evaluate_ref_transforms(json!({"a": 1, "b": 2}), &ops, &state, "").unwrap(),
            json!(true)
        );
        let ops = vec![make_transform("contains", vec![lit(json!("z"))])];
        assert_eq!(
            evaluate_ref_transforms(json!({"a": 1}), &ops, &state, "").unwrap(),
            json!(false)
        );
    }

    // ----- boolean -----

    #[test]
    fn test_and_or_not() {
        let state = empty_state();
        // true and_ 42 → 42
        let ops = vec![make_transform("and_", vec![lit(json!(42))])];
        assert_eq!(
            evaluate_ref_transforms(json!(true), &ops, &state, "").unwrap(),
            json!(42)
        );
        // false or_ 42 → 42
        let ops = vec![make_transform("or_", vec![lit(json!(42))])];
        assert_eq!(
            evaluate_ref_transforms(json!(false), &ops, &state, "").unwrap(),
            json!(42)
        );
        // not_ true → false
        let ops = vec![make_transform("not_", vec![])];
        assert_eq!(
            evaluate_ref_transforms(json!(true), &ops, &state, "").unwrap(),
            json!(false)
        );
    }

    // ----- chained ops -----

    #[test]
    fn test_chained_getitem_add() {
        // node["items"][0] + 10
        let state = empty_state();
        let ops = vec![
            make_transform("getitem", vec![lit(json!("items"))]),
            make_transform("getitem", vec![lit(json!(0))]),
            make_transform("add", vec![lit(json!(10))]),
        ];
        let result =
            evaluate_ref_transforms(json!({"items": [5, 15, 25]}), &ops, &state, "").unwrap();
        assert_eq!(result, json!(15));
    }

    #[test]
    fn test_chained_compare_and() {
        // (value > 10) and_ true → true (if value > 10)
        let state = empty_state();
        let ops = vec![
            make_transform("gt", vec![lit(json!(10))]),
            make_transform("and_", vec![lit(json!(true))]),
        ];
        assert_eq!(
            evaluate_ref_transforms(json!(15), &ops, &state, "").unwrap(),
            json!(true)
        );
        assert_eq!(
            evaluate_ref_transforms(json!(5), &ops, &state, "").unwrap(),
            json!(false)
        );
    }

    // ----- error cases -----

    #[test]
    fn test_div_by_zero() {
        let state = empty_state();
        let ops = vec![make_transform("truediv", vec![lit(json!(0))])];
        assert!(evaluate_ref_transforms(json!(10), &ops, &state, "").is_err());
    }

    #[test]
    fn test_unknown_op() {
        let state = empty_state();
        let ops = vec![make_transform("foobar", vec![])];
        assert!(evaluate_ref_transforms(json!(10), &ops, &state, "").is_err());
    }

    #[test]
    fn test_apply_unsupported() {
        let state = empty_state();
        let ops = vec![make_transform("apply", vec![lit(json!("len"))])];
        assert!(evaluate_ref_transforms(json!([1, 2]), &ops, &state, "").is_err());
    }
}
