//! ex10_multi_model — multi-model comparison, routing, and selection ops.

use hush_serve::hush_op;
use serde_json::{json, Value};

/// is_simple: classification -> is_simple (bool)
#[hush_op]
pub fn is_simple(inputs: &Value) -> Value {
    let classification = inputs["classification"].as_str().unwrap_or("");
    let is_simple = classification.to_uppercase().contains("SIMPLE");
    json!({"is_simple": is_simple})
}

/// compare: a, b -> gpt4o, gpt4o_mini, same_length
#[hush_op]
pub fn compare(inputs: &Value) -> Value {
    let a = inputs["a"].as_str().unwrap_or("");
    let b = inputs["b"].as_str().unwrap_or("");
    let same_length = (a.len() as i64 - b.len() as i64).unsigned_abs() < 50;
    json!({
        "gpt4o": a,
        "gpt4o_mini": b,
        "same_length": same_length,
    })
}

/// select: choice, a1, a2 -> answer, chosen
#[hush_op]
pub fn select(inputs: &Value) -> Value {
    let choice = inputs["choice"].as_str().unwrap_or("");
    let a1 = &inputs["a1"];
    let a2 = &inputs["a2"];
    if choice.contains('1') {
        json!({"answer": a1.clone(), "chosen": "gpt-4o"})
    } else {
        json!({"answer": a2.clone(), "chosen": "gpt-4o-mini"})
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_simple() {
        let result = is_simple(&json!({"classification": "SIMPLE"}));
        assert_eq!(result["is_simple"], true);

        let result = is_simple(&json!({"classification": "COMPLEX"}));
        assert_eq!(result["is_simple"], false);
    }

    #[test]
    fn test_compare() {
        let result = compare(&json!({"a": "Response A", "b": "Response B"}));
        assert_eq!(result["gpt4o"], "Response A");
        assert_eq!(result["gpt4o_mini"], "Response B");
        assert_eq!(result["same_length"], true);
    }

    #[test]
    fn test_select() {
        let result = select(&json!({"choice": "1", "a1": "answer_a", "a2": "answer_b"}));
        assert_eq!(result["answer"], "answer_a");
        assert_eq!(result["chosen"], "gpt-4o");

        let result = select(&json!({"choice": "2", "a1": "answer_a", "a2": "answer_b"}));
        assert_eq!(result["answer"], "answer_b");
        assert_eq!(result["chosen"], "gpt-4o-mini");
    }
}
