//! ex08_error_handling — error capture, routing, retry, and fallback ops.

use hush_serve::hush_op;
use serde_json::{json, Value};

/// failing: () -> simulates ZeroDivisionError
#[hush_op]
pub fn failing(_inputs: &Value) -> Value {
    json!({"result": null, "$error": "ZeroDivisionError: division by zero"})
}

/// safe_divide: a, b -> success, result, error
#[hush_op]
pub fn safe_divide(inputs: &Value) -> Value {
    let a = inputs["a"].as_f64().unwrap_or(0.0);
    let b = inputs["b"].as_f64().unwrap_or(0.0);
    if b == 0.0 {
        json!({"success": false, "result": null, "error": "Cannot divide by zero"})
    } else {
        json!({"success": true, "result": a / b, "error": null})
    }
}

/// handle_success: result -> output = "Result: {result}"
#[hush_op]
pub fn handle_success(inputs: &Value) -> Value {
    let result = inputs["result"].as_f64().unwrap_or(0.0);
    json!({"output": format!("Result: {}", result)})
}

/// handle_error: error -> output = "Error occurred: {error}"
#[hush_op]
pub fn handle_error(inputs: &Value) -> Value {
    let error = inputs["error"].as_str().unwrap_or("unknown");
    json!({"output": format!("Error occurred: {}", error)})
}

/// retry_with_backoff: query -> simulates unreliable API with retry
#[hush_op]
pub fn retry_with_backoff(inputs: &Value) -> Value {
    let query = inputs["query"].as_str().unwrap_or("");
    // Simulate: always succeeds after internal retry (self-contained)
    json!({
        "success": true,
        "answer": format!("Result for: {}", query),
        "attempts": 3,
    })
}

/// with_fallback: primary_result, success -> output, used_fallback
#[hush_op]
pub fn with_fallback(inputs: &Value) -> Value {
    let success = inputs["success"].as_bool().unwrap_or(false);
    if success {
        json!({
            "output": inputs["primary_result"].clone(),
            "used_fallback": false,
        })
    } else {
        json!({
            "output": "Default answer (fallback)",
            "used_fallback": true,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_failing() {
        let result = failing(&json!({}));
        assert!(result["$error"].is_string());
    }

    #[test]
    fn test_safe_divide() {
        let result = safe_divide(&json!({"a": 10, "b": 3}));
        assert_eq!(result["success"], true);
        let result = safe_divide(&json!({"a": 10, "b": 0}));
        assert_eq!(result["success"], false);
    }

    #[test]
    fn test_handle_success() {
        let result = handle_success(&json!({"result": 3.5}));
        assert_eq!(result["output"], "Result: 3.5");
    }

    #[test]
    fn test_handle_error() {
        let result = handle_error(&json!({"error": "something broke"}));
        assert_eq!(result["output"], "Error occurred: something broke");
    }

    #[test]
    fn test_retry_with_backoff() {
        let result = retry_with_backoff(&json!({"query": "test"}));
        assert_eq!(result["success"], true);
        assert_eq!(result["attempts"], 3);
    }

    #[test]
    fn test_with_fallback_success() {
        let result = with_fallback(&json!({"primary_result": "data", "success": true}));
        assert_eq!(result["output"], "data");
        assert_eq!(result["used_fallback"], false);
    }

    #[test]
    fn test_with_fallback_failure() {
        let result = with_fallback(&json!({"primary_result": "data", "success": false}));
        assert_eq!(result["output"], "Default answer (fallback)");
        assert_eq!(result["used_fallback"], true);
    }
}
