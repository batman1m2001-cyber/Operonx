//! Loop condition evaluation — extracted from old WhileOp.
//!
//! Evaluates `until` expressions for GraphOp.loop().
//! Supports simple patterns: `var op literal`, `var op var`,
//! `var` (truthiness), `not var`, `len(var) op literal`.

use serde_json::Value;

use crate::error::RushError;
use crate::refs::ref_transforms::{compare_values, is_truthy};

/// Evaluate a loop's `until` expression against current outputs.
///
/// Returns `true` when the condition is met (loop should stop).
/// Returns `false` on parse error (continue loop), matching Python behavior.
pub(crate) fn evaluate_until_safe(
    op_name: &str,
    until_expr: Option<&str>,
    outputs: &serde_json::Map<String, Value>,
) -> bool {
    let expr = match until_expr {
        Some(e) => e,
        None => return false,
    };

    match evaluate_until(expr, outputs) {
        Ok(val) => val,
        Err(err) => {
            log::warn!("[hush] Loop '{}' condition error: {}", op_name, err);
            false
        }
    }
}

/// Evaluate an `until` expression against a map of values.
///
/// Supports simple patterns:
/// - `var op literal` (e.g., `count >= 5`)
/// - `var op var` (e.g., `score > threshold`)
/// - `var` (e.g., `is_done`) — checks truthiness
/// - `not var` (e.g., `not is_done`)
/// - `len(var) op literal` (e.g., `len(messages) > 10`)
pub(crate) fn evaluate_until(
    expr: &str,
    inputs: &serde_json::Map<String, Value>,
) -> Result<bool, RushError> {
    let expr = expr.trim();

    // Pattern: "not var"
    if let Some(var) = expr.strip_prefix("not ") {
        let var = var.trim();
        return Ok(!is_truthy(inputs.get(var).unwrap_or(&Value::Null)));
    }

    // Pattern: "len(var) op literal"
    if expr.starts_with("len(") {
        return evaluate_len_expr(expr, inputs);
    }

    // Pattern: "var op literal" or "var op var"
    for op_str in &[">=", "<=", "!=", "==", ">", "<"] {
        if let Some((lhs, rhs)) = expr.split_once(op_str) {
            let lhs = lhs.trim();
            let rhs = rhs.trim();
            let lhs_val = resolve_expr_token(lhs, inputs);
            let rhs_val = resolve_expr_token(rhs, inputs);
            return Ok(compare_values(&lhs_val, &rhs_val, op_str));
        }
    }

    // Pattern: bare "var" — check truthiness
    Ok(is_truthy(inputs.get(expr).unwrap_or(&Value::Null)))
}

/// Resolve a token: literal (number, quoted string, true/false/null) or variable name.
/// Also handles Python-style literals: True, False, None.
fn resolve_expr_token(token: &str, inputs: &serde_json::Map<String, Value>) -> Value {
    // Handle Python-style literals (until expressions come from Python DSL)
    match token {
        "True" => return Value::Bool(true),
        "False" => return Value::Bool(false),
        "None" => return Value::Null,
        _ => {}
    }
    if let Ok(v) = serde_json::from_str::<Value>(token) {
        return v;
    }
    inputs.get(token).cloned().unwrap_or(Value::Null)
}

/// Evaluate "len(var) op literal" pattern.
fn evaluate_len_expr(
    expr: &str,
    inputs: &serde_json::Map<String, Value>,
) -> Result<bool, RushError> {
    let after_len = &expr[4..]; // skip "len("
    let close_paren = after_len
        .find(')')
        .ok_or_else(|| RushError::Runtime("Invalid len() expression".into()))?;
    let var = &after_len[..close_paren];
    let rest = after_len[close_paren + 1..].trim();

    let val = inputs.get(var).unwrap_or(&Value::Null);
    let len = match val {
        Value::Array(a) => a.len(),
        Value::String(s) => s.len(),
        Value::Object(o) => o.len(),
        _ => 0,
    };

    for op_str in &[">=", "<=", "!=", "==", ">", "<"] {
        if let Some(rhs) = rest.strip_prefix(op_str) {
            let rhs = rhs.trim();
            let rhs_val: Value = serde_json::from_str(rhs).unwrap_or(Value::Null);
            let len_val = Value::Number(serde_json::Number::from(len));
            return Ok(compare_values(&len_val, &rhs_val, op_str));
        }
    }

    Err(RushError::Runtime(format!(
        "Cannot parse len expression: {expr}"
    )))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn make_map(pairs: &[(&str, Value)]) -> serde_json::Map<String, Value> {
        pairs.iter().map(|(k, v)| (k.to_string(), v.clone())).collect()
    }

    #[test]
    fn test_python_true_literal() {
        let m = make_map(&[("done", json!(true))]);
        assert!(evaluate_until("done == True", &m).unwrap());
    }

    #[test]
    fn test_python_false_literal() {
        let m = make_map(&[("done", json!(false))]);
        assert!(evaluate_until("done == False", &m).unwrap());
        assert!(!evaluate_until("done == True", &m).unwrap());
    }

    #[test]
    fn test_python_none_literal() {
        let m = make_map(&[("x", Value::Null)]);
        assert!(evaluate_until("x == None", &m).unwrap());
    }

    #[test]
    fn test_json_true_also_works() {
        let m = make_map(&[("done", json!(true))]);
        assert!(evaluate_until("done == true", &m).unwrap());
    }

    #[test]
    fn test_counter_ge() {
        let m = make_map(&[("count", json!(5))]);
        assert!(evaluate_until("count >= 5", &m).unwrap());
        assert!(!evaluate_until("count >= 6", &m).unwrap());
    }

    #[test]
    fn test_bare_var_truthiness() {
        let m = make_map(&[("done", json!(true))]);
        assert!(evaluate_until("done", &m).unwrap());
        let m2 = make_map(&[("done", json!(false))]);
        assert!(!evaluate_until("done", &m2).unwrap());
    }

    #[test]
    fn test_not_var() {
        let m = make_map(&[("running", json!(false))]);
        assert!(evaluate_until("not running", &m).unwrap());
    }

    #[test]
    fn test_len_expr() {
        let m = make_map(&[("items", json!([1, 2, 3]))]);
        assert!(evaluate_until("len(items) >= 3", &m).unwrap());
        assert!(!evaluate_until("len(items) > 3", &m).unwrap());
    }
}
