//! ex13_graph — @graph reusable workflow ops (double, add).

use hush_serve::hush_op;
use serde_json::{json, Value};

/// double: x -> result = x * 2
#[hush_op]
pub fn double(inputs: &Value) -> Value {
    if let Some(x) = inputs["x"].as_i64() {
        json!({"result": x * 2})
    } else if let Some(x) = inputs["x"].as_f64() {
        json!({"result": x * 2.0})
    } else {
        json!({"error": "missing or invalid input 'x'"})
    }
}

/// add: a, b -> result = a + b
#[hush_op]
pub fn add(inputs: &Value) -> Value {
    match (inputs["a"].as_i64(), inputs["b"].as_i64()) {
        (Some(a), Some(b)) => json!({"result": a + b}),
        _ => {
            let a = inputs["a"].as_f64().unwrap_or(0.0);
            let b = inputs["b"].as_f64().unwrap_or(0.0);
            json!({"result": a + b})
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_double() {
        let result = double(&json!({"x": 5}));
        assert_eq!(result["result"], 10);
    }

    #[test]
    fn test_add() {
        let result = add(&json!({"a": 3, "b": 7}));
        assert_eq!(result["result"], 10);
    }
}
