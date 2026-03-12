//! Math ops — numeric computations for workflow pipelines.
//!
//! Each function takes a `&serde_json::Value` (the op's inputs as JSON)
//! and returns a `serde_json::Value` (the op's outputs as JSON).
//!
//! Convention: input keys match the Python function's parameter names.

use serde_json::{json, Value};

/// double: x → result = x * 2
///
/// Python equivalent:
/// ```python
/// @op(rust="double")
/// def double(x: int):
///     return {"result": x * 2}
/// ```
pub fn double(inputs: &Value) -> Value {
    if let Some(x) = inputs["x"].as_i64() {
        json!({"result": x * 2})
    } else if let Some(x) = inputs["x"].as_f64() {
        json!({"result": x * 2.0})
    } else {
        json!({"error": "missing or invalid input 'x'"})
    }
}

/// add: a, b → result = a + b
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

/// multiply: a, b → result = a * b
pub fn multiply(inputs: &Value) -> Value {
    match (inputs["a"].as_i64(), inputs["b"].as_i64()) {
        (Some(a), Some(b)) => json!({"result": a * b}),
        _ => {
            let a = inputs["a"].as_f64().unwrap_or(0.0);
            let b = inputs["b"].as_f64().unwrap_or(0.0);
            json!({"result": a * b})
        }
    }
}

/// square: x → result = x * x
pub fn square(inputs: &Value) -> Value {
    if let Some(x) = inputs["x"].as_i64() {
        json!({"result": x * x})
    } else if let Some(x) = inputs["x"].as_f64() {
        json!({"result": x * x})
    } else {
        json!({"error": "missing or invalid input 'x'"})
    }
}

/// math_sum: values (list) → result = sum(values)
pub fn math_sum(inputs: &Value) -> Value {
    let total: f64 = inputs["values"]
        .as_array()
        .map(|arr| arr.iter().filter_map(|v| v.as_f64()).sum())
        .unwrap_or(0.0);
    json!({"result": total})
}

/// math_mean: values (list) → result = mean(values)
pub fn math_mean(inputs: &Value) -> Value {
    let nums: Vec<f64> = inputs["values"]
        .as_array()
        .map(|arr| arr.iter().filter_map(|v| v.as_f64()).collect())
        .unwrap_or_default();
    let mean = if nums.is_empty() {
        0.0
    } else {
        nums.iter().sum::<f64>() / nums.len() as f64
    };
    json!({"result": mean})
}

/// square_named: x → squared = x * x (output key "squared" instead of "result")
pub fn square_named(inputs: &Value) -> Value {
    if let Some(x) = inputs["x"].as_i64() {
        json!({"squared": x * x})
    } else if let Some(x) = inputs["x"].as_f64() {
        json!({"squared": x * x})
    } else {
        json!({"error": "missing or invalid input 'x'"})
    }
}

/// multiply_xy: x, y → product = x * y
pub fn multiply_xy(inputs: &Value) -> Value {
    match (inputs["x"].as_i64(), inputs["y"].as_i64()) {
        (Some(x), Some(y)) => json!({"product": x * y}),
        _ => {
            let x = inputs["x"].as_f64().unwrap_or(0.0);
            let y = inputs["y"].as_f64().unwrap_or(0.0);
            json!({"product": x * y})
        }
    }
}

/// multiply_xy_tagged: x, y → product, $tags (with "large-product" if > 50)
pub fn multiply_xy_tagged(inputs: &Value) -> Value {
    let product = match (inputs["x"].as_i64(), inputs["y"].as_i64()) {
        (Some(x), Some(y)) => x * y,
        _ => {
            let x = inputs["x"].as_f64().unwrap_or(0.0);
            let y = inputs["y"].as_f64().unwrap_or(0.0);
            (x * y) as i64
        }
    };
    let mut tags = vec![json!("multiplied")];
    if product > 50 {
        tags.push(json!("large-product"));
    }
    json!({"product": product, "$tags": tags})
}

/// safe_divide: a, b → success, result, error
pub fn safe_divide(inputs: &Value) -> Value {
    let a = inputs["a"].as_f64().unwrap_or(0.0);
    let b = inputs["b"].as_f64().unwrap_or(0.0);
    if b == 0.0 {
        json!({"success": false, "result": null, "error": "Cannot divide by zero"})
    } else {
        json!({"success": true, "result": a / b, "error": null})
    }
}

/// score_token: token, multiplier → score = len(token) * multiplier
pub fn score_token(inputs: &Value) -> Value {
    let token = inputs["token"].as_str().unwrap_or("");
    let multiplier = inputs["multiplier"].as_i64().unwrap_or(1);
    json!({"score": token.len() as i64 * multiplier})
}

/// sum_list: numbers → total = sum(numbers)
pub fn sum_list(inputs: &Value) -> Value {
    let total: f64 = inputs["numbers"]
        .as_array()
        .map(|arr| arr.iter().filter_map(|v| v.as_f64()).sum())
        .unwrap_or(0.0);
    json!({"total": total})
}

/// calc: x, multiplier → result = x * multiplier
pub fn calc(inputs: &Value) -> Value {
    let x = inputs["x"].as_i64().unwrap_or(0);
    let multiplier = inputs["multiplier"].as_i64().unwrap_or(1);
    json!({"result": x * multiplier})
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

    #[test]
    fn test_math_sum() {
        let result = math_sum(&json!({"values": [1, 2, 3, 4, 5]}));
        assert_eq!(result["result"], 15.0);
    }

    #[test]
    fn test_math_mean() {
        let result = math_mean(&json!({"values": [10, 20, 30]}));
        assert_eq!(result["result"], 20.0);
    }

    #[test]
    fn test_square_named() {
        let result = square_named(&json!({"x": 4}));
        assert_eq!(result["squared"], 16);
    }

    #[test]
    fn test_multiply_xy() {
        let result = multiply_xy(&json!({"x": 3, "y": 7}));
        assert_eq!(result["product"], 21);
    }

    #[test]
    fn test_safe_divide() {
        let result = safe_divide(&json!({"a": 10, "b": 3}));
        assert_eq!(result["success"], true);
        let result = safe_divide(&json!({"a": 10, "b": 0}));
        assert_eq!(result["success"], false);
    }

    #[test]
    fn test_score_token() {
        let result = score_token(&json!({"token": "hello", "multiplier": 3}));
        assert_eq!(result["score"], 15);
    }

    #[test]
    fn test_sum_list() {
        let result = sum_list(&json!({"numbers": [1, 2, 3]}));
        assert_eq!(result["total"], 6.0);
    }

    #[test]
    fn test_calc() {
        let result = calc(&json!({"x": 5, "multiplier": 10}));
        assert_eq!(result["result"], 50);
    }
}
