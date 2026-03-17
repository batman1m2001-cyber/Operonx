//! ex04_llm_advanced — structured output, tool calling, multi-turn chat ops.

use hush_serve::hush_op;
use serde_json::{json, Value};

/// process_response: content, tool_calls -> has_tool_call, tool_result, llm_response
#[hush_op]
pub fn process_response(inputs: &Value) -> Value {
    let content = inputs["content"].as_str().unwrap_or("");
    let tool_calls = inputs["tool_calls"].as_array();

    match tool_calls {
        Some(calls) if !calls.is_empty() => {
            let mut tool_result: Value = Value::Null;

            for call in calls {
                let func_name = call["function"]["name"].as_str().unwrap_or("");
                if func_name == "calculator" {
                    let args = &call["function"]["arguments"];
                    let a = args["a"].as_f64().unwrap_or(0.0);
                    let b = args["b"].as_f64().unwrap_or(0.0);
                    let op = args["operation"].as_str().unwrap_or("add");
                    let result = match op {
                        "add" => a + b,
                        "subtract" => a - b,
                        "multiply" => a * b,
                        "divide" => {
                            if b != 0.0 {
                                a / b
                            } else {
                                f64::NAN
                            }
                        }
                        _ => a + b,
                    };
                    tool_result = json!(result);
                } else {
                    tool_result = json!(format!("Simulated result for {}", func_name));
                }
            }

            json!({
                "has_tool_call": true,
                "tool_result": tool_result,
                "llm_response": content,
            })
        }
        _ => {
            json!({
                "has_tool_call": false,
                "tool_result": null,
                "llm_response": content,
            })
        }
    }
}

/// update_history: history, message, response -> new_history
#[hush_op]
pub fn update_history(inputs: &Value) -> Value {
    let mut new_history: Vec<Value> = inputs["history"]
        .as_array()
        .cloned()
        .unwrap_or_default();

    let message = inputs["message"].as_str().unwrap_or("");
    let response = inputs["response"].as_str().unwrap_or("");

    new_history.push(json!({"role": "user", "content": message}));
    new_history.push(json!({"role": "assistant", "content": response}));

    json!({"new_history": new_history})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_process_response_no_tools() {
        let result = process_response(&json!({
            "content": "Hello!",
            "tool_calls": [],
        }));
        assert_eq!(result["has_tool_call"], false);
        assert!(result["tool_result"].is_null());
        assert_eq!(result["llm_response"], "Hello!");
    }

    #[test]
    fn test_process_response_calculator() {
        let result = process_response(&json!({
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "calculator",
                    "arguments": {"a": 3, "b": 4, "operation": "multiply"}
                }
            }],
        }));
        assert_eq!(result["has_tool_call"], true);
        assert_eq!(result["tool_result"], 12.0);
    }

    #[test]
    fn test_update_history() {
        let result = update_history(&json!({
            "history": [{"role": "system", "content": "You are helpful."}],
            "message": "Hi",
            "response": "Hello!",
        }));
        let history = result["new_history"].as_array().unwrap();
        assert_eq!(history.len(), 3);
        assert_eq!(history[1]["role"], "user");
        assert_eq!(history[2]["role"], "assistant");
    }
}
