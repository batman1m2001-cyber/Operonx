//! Pipeline ops — workflow step functions for Hush example pipelines.
//!
//! Each function takes a `&serde_json::Value` (the op's inputs as JSON)
//! and returns a `serde_json::Value` (the op's outputs as JSON).
//!
//! Convention: input keys match the Python function's parameter names.

use serde_json::{json, Value};
use hush_serve::hush_op;

// ---------------------------------------------------------------------------
// 1. step_a
// ---------------------------------------------------------------------------

/// step_a: () → {"a_result": "Kết quả A"}
#[hush_op]
pub fn step_a(_inputs: &Value) -> Value {
    json!({"a_result": "Kết quả A"})
}

// ---------------------------------------------------------------------------
// 2. step_b
// ---------------------------------------------------------------------------

/// step_b: () → {"b_result": "Kết quả B"}
#[hush_op]
pub fn step_b(_inputs: &Value) -> Value {
    json!({"b_result": "Kết quả B"})
}

// ---------------------------------------------------------------------------
// 3. merge_two
// ---------------------------------------------------------------------------

/// merge_two: a, b → {"combined": "{a} + {b}"}
#[hush_op]
pub fn merge_two(inputs: &Value) -> Value {
    let a = inputs["a"].as_str().unwrap_or("");
    let b = inputs["b"].as_str().unwrap_or("");
    json!({"combined": format!("{} + {}", a, b)})
}

// ---------------------------------------------------------------------------
// 4. fetch_data
// ---------------------------------------------------------------------------

/// fetch_data: () → {"data": [1, 2, 3, 4, 5]}
#[hush_op]
pub fn fetch_data(_inputs: &Value) -> Value {
    json!({"data": [1, 2, 3, 4, 5]})
}

// ---------------------------------------------------------------------------
// 5. transform_double
// ---------------------------------------------------------------------------

/// transform_double: data (array) → {"transformed": [x*2 for x in data]}
#[hush_op]
pub fn transform_double(inputs: &Value) -> Value {
    let transformed: Vec<Value> = inputs["data"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .map(|v| {
                    if let Some(n) = v.as_i64() {
                        json!(n * 2)
                    } else if let Some(n) = v.as_f64() {
                        json!(n * 2.0)
                    } else {
                        v.clone()
                    }
                })
                .collect()
        })
        .unwrap_or_default();
    json!({"transformed": transformed})
}

// ---------------------------------------------------------------------------
// 6. merge_analysis
// ---------------------------------------------------------------------------

/// merge_analysis: s, k, wc, cc, awl → {"analysis": {...}}
#[hush_op]
pub fn merge_analysis(inputs: &Value) -> Value {
    json!({
        "analysis": {
            "sentiment": inputs["s"].clone(),
            "keywords": inputs["k"].clone(),
            "word_count": inputs["wc"].clone(),
            "char_count": inputs["cc"].clone(),
            "avg_word_len": inputs["awl"].clone(),
        }
    })
}

// ---------------------------------------------------------------------------
// 7. merge_results
// ---------------------------------------------------------------------------

/// merge_results: a, b → {"gpt4o": a, "gpt4o_mini": b, "same_length": abs(len(a)-len(b)) < 50}
#[hush_op]
pub fn merge_results(inputs: &Value) -> Value {
    let a = inputs["a"].as_str().unwrap_or("");
    let b = inputs["b"].as_str().unwrap_or("");
    let same_length = (a.len() as i64 - b.len() as i64).unsigned_abs() < 50;
    json!({
        "gpt4o": a,
        "gpt4o_mini": b,
        "same_length": same_length,
    })
}


// ---------------------------------------------------------------------------
// 8. safe_process
// ---------------------------------------------------------------------------

/// safe_process: item → if odd: {"result": item*10, "error": null}
///                       else:  {"result": null, "error": "Even number: {item}"}
#[hush_op]
pub fn safe_process(inputs: &Value) -> Value {
    let item = inputs["item"].as_i64().unwrap_or(0);
    if item % 2 != 0 {
        json!({"result": item * 10, "error": null})
    } else {
        json!({"result": null, "error": format!("Even number: {}", item)})
    }
}

// ---------------------------------------------------------------------------
// 9. filter_results
// ---------------------------------------------------------------------------

/// filter_results: results, errors → {"successful": [...], "failed": [...]}
///
/// Pairs up results and errors by index.
/// If error is null, the corresponding result goes into "successful".
/// Otherwise, the error string goes into "failed".
#[hush_op]
pub fn filter_results(inputs: &Value) -> Value {
    let results = inputs["results"].as_array();
    let errors = inputs["errors"].as_array();

    let mut successful = Vec::new();
    let mut failed = Vec::new();

    if let (Some(results), Some(errors)) = (results, errors) {
        for (r, e) in results.iter().zip(errors.iter()) {
            if e.is_null() {
                successful.push(r.clone());
            } else {
                failed.push(e.clone());
            }
        }
    }

    json!({"successful": successful, "failed": failed})
}

// ---------------------------------------------------------------------------
// 10. classify_simple
// ---------------------------------------------------------------------------

/// classify_simple: classification → {"is_simple": bool}
///
/// Checks if the classification string contains "SIMPLE" (case-insensitive).
#[hush_op]
pub fn classify_simple(inputs: &Value) -> Value {
    let classification = inputs["classification"].as_str().unwrap_or("");
    let is_simple = classification.to_uppercase().contains("SIMPLE");
    json!({"is_simple": is_simple})
}

// ---------------------------------------------------------------------------
// 11. process_response_tool
// ---------------------------------------------------------------------------

/// process_response_tool: content, tool_calls → {"has_tool_call": bool, "tool_result": ..., "llm_response": content}
///
/// If tool_calls is a non-empty array, has_tool_call=true and we simulate tool execution.
/// If a tool_call has function.name == "calculator", compute result from args.
#[hush_op]
pub fn process_response_tool(inputs: &Value) -> Value {
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

// ---------------------------------------------------------------------------
// 11. update_history
// ---------------------------------------------------------------------------

/// update_history: history, message, response → {"new_history": history + [user msg, assistant msg]}
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

// ---------------------------------------------------------------------------
// 12. validate_input
// ---------------------------------------------------------------------------

/// validate_input: x → {"validated_x": x, "$tags": ["validated"]}
#[hush_op]
pub fn validate_input(inputs: &Value) -> Value {
    json!({
        "validated_x": inputs["x"].clone(),
        "$tags": ["validated"],
    })
}

// ---------------------------------------------------------------------------
// 13. with_fallback
// ---------------------------------------------------------------------------

/// with_fallback: primary_result, success → if success: {"output": primary_result, "used_fallback": false}
///                                          else:        {"output": "Default answer (fallback)", "used_fallback": true}
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

// ---------------------------------------------------------------------------
// 14. init_agent
// ---------------------------------------------------------------------------

/// init_agent: query → {"messages": [...], "done": false, "answer": ""}
#[hush_op]
pub fn init_agent(inputs: &Value) -> Value {
    let query = inputs["query"].as_str().unwrap_or("");
    json!({
        "messages": [
            {"role": "system", "content": "You are a helpful assistant. Answer concisely."},
            {"role": "user", "content": query},
        ],
        "done": false,
        "answer": "",
    })
}

// ---------------------------------------------------------------------------
// 15. process_agent_response
// ---------------------------------------------------------------------------

/// process_agent_response: content, tool_calls, messages → process response, update messages, return done/answer
#[hush_op]
pub fn process_agent_response(inputs: &Value) -> Value {
    let content = inputs["content"].as_str().unwrap_or("");
    let tool_calls = inputs["tool_calls"].as_array();
    let mut messages: Vec<Value> = inputs["messages"]
        .as_array()
        .cloned()
        .unwrap_or_default();

    match tool_calls {
        Some(calls) if !calls.is_empty() => {
            // Append assistant message with tool calls
            messages.push(json!({
                "role": "assistant",
                "content": content,
                "tool_calls": calls,
            }));

            // Execute tools and append results
            for call in calls {
                let func_name = call["function"]["name"].as_str().unwrap_or("");
                let args_str = call["function"]["arguments"].as_str().unwrap_or("{}");
                let tool_result = execute_tool(func_name, args_str);
                messages.push(json!({
                    "role": "tool",
                    "content": tool_result,
                    "tool_call_id": call["id"].as_str().unwrap_or(""),
                }));
            }

            json!({
                "messages": messages,
                "done": false,
                "answer": "",
            })
        }
        _ => {
            // No tool calls — final answer
            messages.push(json!({
                "role": "assistant",
                "content": content,
            }));

            json!({
                "messages": messages,
                "done": true,
                "answer": content,
            })
        }
    }
}

/// Execute a tool by name. Mirrors the Python TOOLS dict in workflow.py.
fn execute_tool(name: &str, args_json: &str) -> String {
    let args: Value = serde_json::from_str(args_json).unwrap_or(json!({}));
    match name {
        "calculator" => {
            let expr = args["expression"].as_str().unwrap_or("");
            // Simple math eval: parse with f64 arithmetic
            match eval_math(expr) {
                Ok(result) => json!({"result": result.to_string()}).to_string(),
                Err(e) => json!({"error": e}).to_string(),
            }
        }
        "search" => {
            let query = args["query"].as_str().unwrap_or("").to_lowercase();
            let knowledge = [
                ("python", "Python is a high-level programming language created by Guido van Rossum in 1991."),
                ("hush", "Hush is an async workflow orchestration engine for GenAI applications."),
                ("vietnam", "Vietnam is a country in Southeast Asia. Capital: Hanoi. Population: ~100 million."),
                ("machine learning", "Machine learning is a subset of AI that learns patterns from data."),
            ];
            let result = knowledge.iter()
                .find(|(key, _)| query.contains(key))
                .map(|(_, v)| *v)
                .unwrap_or("No information found.");
            json!({"result": result}).to_string()
        }
        _ => json!({"error": format!("Unknown tool: {}", name)}).to_string(),
    }
}

/// Minimal math expression evaluator for calculator tool.
/// Handles +, -, *, / with operator precedence.
fn eval_math(expr: &str) -> Result<f64, String> {
    let expr = expr.trim();
    if expr.is_empty() {
        return Err("Empty expression".to_string());
    }

    // Tokenize
    let mut tokens: Vec<String> = Vec::new();
    let mut num_buf = String::new();
    for ch in expr.chars() {
        if ch.is_ascii_digit() || ch == '.' || (ch == '-' && num_buf.is_empty() && (tokens.is_empty() || matches!(tokens.last().map(|s| s.as_str()), Some("+"|"-"|"*"|"/"|"(")))) {
            num_buf.push(ch);
        } else if ch == '+' || ch == '-' || ch == '*' || ch == '/' || ch == '(' || ch == ')' {
            if !num_buf.is_empty() {
                tokens.push(std::mem::take(&mut num_buf));
            }
            tokens.push(ch.to_string());
        } else if ch.is_whitespace() {
            if !num_buf.is_empty() {
                tokens.push(std::mem::take(&mut num_buf));
            }
        } else {
            return Err(format!("Invalid character: {}", ch));
        }
    }
    if !num_buf.is_empty() {
        tokens.push(num_buf);
    }

    // Shunting-yard → RPN
    let mut output: Vec<String> = Vec::new();
    let mut ops: Vec<String> = Vec::new();
    let precedence = |op: &str| -> i32 {
        match op { "+" | "-" => 1, "*" | "/" => 2, _ => 0 }
    };
    for tok in &tokens {
        match tok.as_str() {
            "+" | "-" | "*" | "/" => {
                while let Some(top) = ops.last() {
                    if top != "(" && precedence(top) >= precedence(tok) {
                        output.push(ops.pop().unwrap());
                    } else { break; }
                }
                ops.push(tok.clone());
            }
            "(" => ops.push(tok.clone()),
            ")" => {
                while let Some(top) = ops.pop() {
                    if top == "(" { break; }
                    output.push(top);
                }
            }
            _ => output.push(tok.clone()),
        }
    }
    while let Some(op) = ops.pop() { output.push(op); }

    // Evaluate RPN
    let mut stack: Vec<f64> = Vec::new();
    for tok in &output {
        match tok.as_str() {
            "+" => { let b = stack.pop().ok_or("Stack underflow")?; let a = stack.pop().ok_or("Stack underflow")?; stack.push(a + b); }
            "-" => { let b = stack.pop().ok_or("Stack underflow")?; let a = stack.pop().ok_or("Stack underflow")?; stack.push(a - b); }
            "*" => { let b = stack.pop().ok_or("Stack underflow")?; let a = stack.pop().ok_or("Stack underflow")?; stack.push(a * b); }
            "/" => { let b = stack.pop().ok_or("Stack underflow")?; let a = stack.pop().ok_or("Stack underflow")?; if b == 0.0 { return Err("Division by zero".to_string()); } stack.push(a / b); }
            _ => { let n: f64 = tok.parse().map_err(|_| format!("Invalid number: {}", tok))?; stack.push(n); }
        }
    }
    stack.pop().ok_or("Empty expression".to_string())
}

// ---------------------------------------------------------------------------
// 16. excellent
// ---------------------------------------------------------------------------

/// excellent: () → {"grade": "A", "message": "Xuất sắc!"}
#[hush_op]
pub fn excellent(_inputs: &Value) -> Value {
    json!({"grade": "A", "message": "Xuất sắc!"})
}

// ---------------------------------------------------------------------------
// 17. good
// ---------------------------------------------------------------------------

/// good: () → {"grade": "B", "message": "Tốt!"}
#[hush_op]
pub fn good(_inputs: &Value) -> Value {
    json!({"grade": "B", "message": "Tốt!"})
}

// ---------------------------------------------------------------------------
// 18. average_grade
// ---------------------------------------------------------------------------

/// average_grade: () → {"grade": "C", "message": "Trung bình"}
#[hush_op]
pub fn average_grade(_inputs: &Value) -> Value {
    json!({"grade": "C", "message": "Trung bình"})
}

// ---------------------------------------------------------------------------
// 19. fail_grade
// ---------------------------------------------------------------------------

/// fail_grade: () → {"grade": "F", "message": "Cần cải thiện"}
#[hush_op]
pub fn fail_grade(_inputs: &Value) -> Value {
    json!({"grade": "F", "message": "Cần cải thiện"})
}

// ---------------------------------------------------------------------------
// 20. get_config
// ---------------------------------------------------------------------------

/// get_config: () → {"multiplier": 10}
#[hush_op]
pub fn get_config(_inputs: &Value) -> Value {
    json!({"multiplier": 10})
}

// ---------------------------------------------------------------------------
// 21. handle_intent
// ---------------------------------------------------------------------------

/// handle_intent: intent, transcript → response based on intent
#[hush_op]
pub fn handle_intent(inputs: &Value) -> Value {
    let intent = inputs["intent"].as_str().unwrap_or("");
    let transcript = inputs["transcript"].as_str().unwrap_or("");

    if intent == "greeting" {
        json!({
            "response": "Hello! How can I help you today?",
        })
    } else {
        json!({
            "response": format!("I understand. Let me help with: {}", transcript),
        })
    }
}

// ---------------------------------------------------------------------------
// 22. process_item_squared
// ---------------------------------------------------------------------------

/// process_item_squared: item → {"result": item*item, "status": "ok"}
#[hush_op]
pub fn process_item_squared(inputs: &Value) -> Value {
    let item = inputs["item"].as_i64().unwrap_or(0);
    json!({"result": item * item, "status": "ok"})
}

// ---------------------------------------------------------------------------
// 23. select_answer
// ---------------------------------------------------------------------------

/// select_answer: choice, a1, a2 → {"answer": ..., "chosen": ...}
///
/// Selects between two answers based on choice string containing "1".
#[hush_op]
pub fn select_answer(inputs: &Value) -> Value {
    let choice = inputs["choice"].as_str().unwrap_or("");
    let a1 = &inputs["a1"];
    let a2 = &inputs["a2"];
    if choice.contains('1') {
        json!({"answer": a1.clone(), "chosen": "gpt-4o"})
    } else {
        json!({"answer": a2.clone(), "chosen": "gpt-4o-mini"})
    }
}

// ---------------------------------------------------------------------------
// 24. failing_op
// ---------------------------------------------------------------------------

/// failing_op: () → panics with ZeroDivisionError equivalent
///
/// Simulates a Python ZeroDivisionError by returning an error value.
/// In Rust plugin context, we return an error output that hush-icore treats as op failure.
#[hush_op]
pub fn failing_op(_inputs: &Value) -> Value {
    json!({"result": null, "$error": "ZeroDivisionError: division by zero"})
}

// ---------------------------------------------------------------------------
// 25. retry_with_backoff
// ---------------------------------------------------------------------------

/// retry_with_backoff: query → simulates unreliable API with retry
///
/// Mirrors Python: fails first 2 attempts, succeeds on 3rd.
/// Self-contained (no global state).
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

// ---------------------------------------------------------------------------
// 26. aggregate
// ---------------------------------------------------------------------------

/// aggregate: data (list of numbers) → total, average, count
#[hush_op]
pub fn aggregate(inputs: &Value) -> Value {
    let empty = vec![];
    let data = inputs["data"].as_array().unwrap_or(&empty);
    let nums: Vec<f64> = data.iter().filter_map(|v| v.as_f64()).collect();
    let total: f64 = nums.iter().sum();
    let count = nums.len();
    let average = if count > 0 { total / count as f64 } else { 0.0 };
    json!({"total": total as i64, "average": average, "count": count})
}

// ---------------------------------------------------------------------------
// 27. compare
// ---------------------------------------------------------------------------

/// compare: results → comparison (pass-through for multi-model comparison)
#[hush_op]
pub fn compare(inputs: &Value) -> Value {
    let results = inputs.get("results").cloned().unwrap_or(json!({}));
    json!({"comparison": results})
}

// ===========================================================================
// Tests
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;

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
    fn test_merge_two() {
        let result = merge_two(&json!({"a": "Hello", "b": "World"}));
        assert_eq!(result["combined"], "Hello + World");
    }

    #[test]
    fn test_fetch_data() {
        let result = fetch_data(&json!({}));
        assert_eq!(result["data"], json!([1, 2, 3, 4, 5]));
    }

    #[test]
    fn test_transform_double() {
        let result = transform_double(&json!({"data": [1, 2, 3, 4, 5]}));
        assert_eq!(result["transformed"], json!([2, 4, 6, 8, 10]));
    }

    #[test]
    fn test_merge_analysis() {
        let result = merge_analysis(&json!({
            "s": "positive",
            "k": ["rust", "hush"],
            "wc": 100,
            "cc": 500,
            "awl": 5.0,
        }));
        assert_eq!(result["analysis"]["sentiment"], "positive");
        assert_eq!(result["analysis"]["keywords"], json!(["rust", "hush"]));
        assert_eq!(result["analysis"]["word_count"], 100);
        assert_eq!(result["analysis"]["char_count"], 500);
        assert_eq!(result["analysis"]["avg_word_len"], 5.0);
    }

    #[test]
    fn test_merge_results() {
        let result = merge_results(&json!({"a": "Response A", "b": "Response B"}));
        assert_eq!(result["gpt4o"], "Response A");
        assert_eq!(result["gpt4o_mini"], "Response B");
        assert_eq!(result["same_length"], true);
    }

    #[test]
    fn test_safe_process_odd() {
        let result = safe_process(&json!({"item": 3}));
        assert_eq!(result["result"], 30);
        assert!(result["error"].is_null());
    }

    #[test]
    fn test_safe_process_even() {
        let result = safe_process(&json!({"item": 4}));
        assert!(result["result"].is_null());
        assert_eq!(result["error"], "Even number: 4");
    }

    #[test]
    fn test_filter_results() {
        let result = filter_results(&json!({
            "results": [10, null, 30],
            "errors": [null, "Even number: 2", null],
        }));
        assert_eq!(result["successful"], json!([10, 30]));
        assert_eq!(result["failed"], json!(["Even number: 2"]));
    }

    #[test]
    fn test_process_response_tool_no_tools() {
        let result = process_response_tool(&json!({
            "content": "Hello!",
            "tool_calls": [],
        }));
        assert_eq!(result["has_tool_call"], false);
        assert!(result["tool_result"].is_null());
        assert_eq!(result["llm_response"], "Hello!");
    }

    #[test]
    fn test_process_response_tool_calculator() {
        let result = process_response_tool(&json!({
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
        assert_eq!(history[1]["content"], "Hi");
        assert_eq!(history[2]["role"], "assistant");
        assert_eq!(history[2]["content"], "Hello!");
    }

    #[test]
    fn test_validate_input() {
        let result = validate_input(&json!({"x": 42}));
        assert_eq!(result["validated_x"], 42);
        assert_eq!(result["$tags"], json!(["validated"]));
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

    #[test]
    fn test_init_agent() {
        let result = init_agent(&json!({"query": "What is Rust?"}));
        let messages = result["messages"].as_array().unwrap();
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0]["role"], "system");
        assert_eq!(messages[1]["content"], "What is Rust?");
        assert_eq!(result["done"], false);
        assert_eq!(result["answer"], "");
    }

    #[test]
    fn test_process_agent_response_final() {
        let result = process_agent_response(&json!({
            "content": "Rust is a systems language.",
            "tool_calls": [],
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "What is Rust?"},
            ],
        }));
        assert_eq!(result["done"], true);
        assert_eq!(result["answer"], "Rust is a systems language.");
        assert_eq!(result["messages"].as_array().unwrap().len(), 3);
    }

    #[test]
    fn test_process_agent_response_tool_call() {
        let result = process_agent_response(&json!({
            "content": "",
            "tool_calls": [{"id": "call_1", "function": {"name": "calculator", "arguments": "{\"expression\": \"25 * 4 + 100\"}"}}],
            "messages": [
                {"role": "user", "content": "What is 25 * 4 + 100?"},
            ],
        }));
        assert_eq!(result["done"], false);
        assert_eq!(result["answer"], "");
        // messages: original 1 + assistant + tool = 3
        assert_eq!(result["messages"].as_array().unwrap().len(), 3);
        // Verify tool actually computed the result
        let tool_msg = &result["messages"][2];
        assert_eq!(tool_msg["role"], "tool");
        let tool_content: Value = serde_json::from_str(tool_msg["content"].as_str().unwrap()).unwrap();
        assert_eq!(tool_content["result"], "200");
    }

    #[test]
    fn test_process_agent_response_search() {
        let result = process_agent_response(&json!({
            "content": "",
            "tool_calls": [{"id": "call_2", "function": {"name": "search", "arguments": "{\"query\": \"python\"}"}}],
            "messages": [
                {"role": "user", "content": "Tell me about Python"},
            ],
        }));
        assert_eq!(result["done"], false);
        let tool_msg = &result["messages"][2];
        let tool_content: Value = serde_json::from_str(tool_msg["content"].as_str().unwrap()).unwrap();
        assert!(tool_content["result"].as_str().unwrap().contains("Guido van Rossum"));
    }

    #[test]
    fn test_eval_math() {
        assert_eq!(eval_math("25 * 4 + 100").unwrap(), 200.0);
        assert_eq!(eval_math("15 * 7").unwrap(), 105.0);
        assert_eq!(eval_math("(2 + 3) * 4").unwrap(), 20.0);
        assert_eq!(eval_math("10 / 3").unwrap(), 10.0 / 3.0);
    }

    #[test]
    fn test_excellent() {
        let result = excellent(&json!({}));
        assert_eq!(result["grade"], "A");
        assert_eq!(result["message"], "Xuất sắc!");
    }

    #[test]
    fn test_good() {
        let result = good(&json!({}));
        assert_eq!(result["grade"], "B");
        assert_eq!(result["message"], "Tốt!");
    }

    #[test]
    fn test_average_grade() {
        let result = average_grade(&json!({}));
        assert_eq!(result["grade"], "C");
        assert_eq!(result["message"], "Trung bình");
    }

    #[test]
    fn test_fail_grade() {
        let result = fail_grade(&json!({}));
        assert_eq!(result["grade"], "F");
        assert_eq!(result["message"], "Cần cải thiện");
    }

    #[test]
    fn test_get_config() {
        let result = get_config(&json!({}));
        assert_eq!(result["multiplier"], 10);
    }

    #[test]
    fn test_handle_intent_greeting() {
        let result = handle_intent(&json!({"intent": "greeting", "transcript": "xin chào"}));
        assert_eq!(result["response"], "Hello! How can I help you today?");
    }

    #[test]
    fn test_handle_intent_general() {
        let result = handle_intent(&json!({"intent": "question", "transcript": "thời tiết"}));
        assert!(result["response"].as_str().unwrap().contains("thời tiết"));
    }

    #[test]
    fn test_process_item_squared() {
        let result = process_item_squared(&json!({"item": 7}));
        assert_eq!(result["result"], 49);
        assert_eq!(result["status"], "ok");
    }
}
