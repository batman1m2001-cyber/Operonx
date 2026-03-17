//! ex01_hello_world — greeting, uppercase, parallel merge ops.

use hush_serve::hush_op;
use serde_json::{json, Value};

/// greet: name -> greeting = "Xin chao, {name}!"
#[hush_op]
pub fn greet(inputs: &Value) -> Value {
    let name = inputs["name"].as_str().unwrap_or("World");
    json!({"greeting": format!("Xin chào, {}!", name)})
}

/// greet_en: name -> greeting = "Hello, {name}!"
#[hush_op]
pub fn greet_en(inputs: &Value) -> Value {
    let name = inputs["name"].as_str().unwrap_or("World");
    json!({"greeting": format!("Hello, {}!", name)})
}

/// upper: text -> result = text.upper()
#[hush_op]
pub fn upper(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    json!({"result": text.to_uppercase()})
}

/// step_a: () -> {"a_result": "Ket qua A"}
#[hush_op]
pub fn step_a(_inputs: &Value) -> Value {
    json!({"a_result": "Kết quả A"})
}

/// step_b: () -> {"b_result": "Ket qua B"}
#[hush_op]
pub fn step_b(_inputs: &Value) -> Value {
    json!({"b_result": "Kết quả B"})
}

/// merge: a, b -> {"combined": "{a} + {b}"}
#[hush_op]
pub fn merge(inputs: &Value) -> Value {
    let a = inputs["a"].as_str().unwrap_or("");
    let b = inputs["b"].as_str().unwrap_or("");
    json!({"combined": format!("{} + {}", a, b)})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_greet() {
        let result = greet(&json!({"name": "Hush"}));
        assert_eq!(result["greeting"], "Xin chào, Hush!");
    }

    #[test]
    fn test_greet_en() {
        let result = greet_en(&json!({"name": "Hush"}));
        assert_eq!(result["greeting"], "Hello, Hush!");
    }

    #[test]
    fn test_upper() {
        let result = upper(&json!({"text": "hello world"}));
        assert_eq!(result["result"], "HELLO WORLD");
    }

    #[test]
    fn test_step_a() {
        let result = step_a(&json!({}));
        assert_eq!(result["a_result"], "Kết quả A");
    }

    #[test]
    fn test_step_b() {
        let result = step_b(&json!({}));
        assert_eq!(result["b_result"], "Kết quả B");
    }

    #[test]
    fn test_merge() {
        let result = merge(&json!({"a": "Hello", "b": "World"}));
        assert_eq!(result["combined"], "Hello + World");
    }
}
