//! WhileOp execution — loop until condition or max iterations.
//!
//! Mirrors Python's `ops/iteration/while_op.py` (WhileOp).
//! Pure Rust on serde_json::Value — no Python/GIL needed.
//! Uses a simple expression parser for `until` conditions instead of Python eval().

use serde_json::Value;

use crate::config::OpConfig;
use crate::error::RushError;
use crate::ops::base;
use crate::ops::graph::graph_op;
use crate::ops::iteration::helpers;
use crate::refs::interpreter::{compare_values, is_truthy};
use crate::states::state::EngineState;

/// Execute a WhileOp: loop until condition is True or max_iterations reached.
pub(crate) fn run(
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> Result<(), RushError> {
    let inner = op.inner_graph.as_ref().ok_or_else(|| {
        RushError::IterationError(format!(
            "WhileOp '{}' missing inner_graph config",
            op.full_name
        ))
    })?;
    let iter_config = op.iteration_config.as_ref().ok_or_else(|| {
        RushError::IterationError(format!(
            "WhileOp '{}' missing iteration_config",
            op.full_name
        ))
    })?;

    let max_iterations = iter_config.max_iterations.unwrap_or(100);
    let until_expr = iter_config.until.as_deref();

    // 1. Resolve initial inputs into step_inputs map
    let mut step_inputs = serde_json::Map::new();
    for param in &op.inputs {
        if let Some(value) = base::resolve_param(param, state, context)? {
            step_inputs.insert(param.var_name.clone(), value);
        }
    }

    // Also resolve broadcast values
    for param in &iter_config.broadcast {
        if let Some(value) = base::resolve_iter_param(param, state, context)? {
            step_inputs.insert(param.var_name.clone(), value);
        }
    }

    // 2. Evaluate initial condition
    let mut should_stop = evaluate_condition_safe(&op.full_name, until_expr, &step_inputs);

    let ctx_prefix = helpers::context_prefix(context);
    let mut step_count: usize = 0;

    // 3. Loop
    while !should_stop && step_count < max_iterations {
        let step_context = format!("{}[{}]", ctx_prefix, step_count);

        // Store step_inputs into state
        for (key, value) in &step_inputs {
            state.set(&op.full_name, key, &step_context, value.clone());
        }

        // Run inner graph
        graph_op::run_graph(inner, state, &step_context)?;

        // Collect outputs and merge into step_inputs
        let outputs = graph_op::get_outputs(inner, state, &step_context)?;
        if let Value::Object(outputs_map) = outputs {
            for (k, v) in outputs_map {
                step_inputs.insert(k, v);
            }
        }

        step_count += 1;

        // Re-evaluate condition
        should_stop = evaluate_condition_safe(&op.full_name, until_expr, &step_inputs);
    }

    // 4. Store final step_inputs as outputs in parent context
    for (key, value) in &step_inputs {
        state.set(&op.full_name, key, context, value.clone());
    }

    // 5. Add iteration_metrics
    let metrics = serde_json::json!({
        "total_iterations": step_count,
        "success_count": step_count,
        "error_count": 0,
        "max_iterations_reached": step_count >= max_iterations,
        "stopped_by_condition": should_stop,
    });
    state.set(&op.full_name, "iteration_metrics", context, metrics);

    // 6. Push output refs
    base::push_output_refs(op, state, context)?;

    Ok(())
}

// =============================================================================
// Until expression evaluation
// =============================================================================

/// Evaluate the `until` condition, catching errors and logging warnings.
/// Returns `false` on error (continue loop), matching Python's behavior.
fn evaluate_condition_safe(
    op_name: &str,
    until_expr: Option<&str>,
    inputs: &serde_json::Map<String, Value>,
) -> bool {
    let expr = match until_expr {
        Some(e) => e,
        None => return false,
    };

    match evaluate_until(expr, inputs) {
        Ok(val) => val,
        Err(err) => {
            log::warn!(
                "[rush] WhileOp '{}' condition error: {}",
                op_name,
                err
            );
            false
        }
    }
}

/// Evaluate a WhileOp's `until` expression against current step_inputs.
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
    // Try operators from longest to shortest to avoid partial matches
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

/// Resolve a token: if it looks like a literal (number, quoted string, true/false/null),
/// parse it; otherwise look up as variable name in inputs.
fn resolve_expr_token(token: &str, inputs: &serde_json::Map<String, Value>) -> Value {
    // Try parsing as JSON literal
    if let Ok(v) = serde_json::from_str::<Value>(token) {
        return v;
    }
    // Otherwise look up as variable
    inputs.get(token).cloned().unwrap_or(Value::Null)
}

/// Evaluate "len(var) op literal" pattern.
fn evaluate_len_expr(
    expr: &str,
    inputs: &serde_json::Map<String, Value>,
) -> Result<bool, RushError> {
    // Extract var name from len(...)
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

    // Parse "op literal" from rest
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
