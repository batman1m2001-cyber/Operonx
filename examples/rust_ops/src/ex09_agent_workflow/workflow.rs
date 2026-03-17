//! ex09_agent_workflow — agent init, tool execution, and response processing ops.

use hush_serve::hush_op;
use serde_json::{json, Value};

/// init_agent: query -> messages, done, answer
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

/// process_response: content, tool_calls, messages -> messages, done, answer
#[hush_op]
pub fn process_response(inputs: &Value) -> Value {
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

    // Shunting-yard -> RPN
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

#[cfg(test)]
mod tests {
    use super::*;

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
    fn test_process_response_final() {
        let result = process_response(&json!({
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
    fn test_process_response_tool_call() {
        let result = process_response(&json!({
            "content": "",
            "tool_calls": [{"id": "call_1", "function": {"name": "calculator", "arguments": "{\"expression\": \"25 * 4 + 100\"}"}}],
            "messages": [
                {"role": "user", "content": "What is 25 * 4 + 100?"},
            ],
        }));
        assert_eq!(result["done"], false);
        assert_eq!(result["messages"].as_array().unwrap().len(), 3);
        let tool_msg = &result["messages"][2];
        assert_eq!(tool_msg["role"], "tool");
        let tool_content: Value = serde_json::from_str(tool_msg["content"].as_str().unwrap()).unwrap();
        assert_eq!(tool_content["result"], "200");
    }

    #[test]
    fn test_eval_math() {
        assert_eq!(eval_math("25 * 4 + 100").unwrap(), 200.0);
        assert_eq!(eval_math("(2 + 3) * 4").unwrap(), 20.0);
    }
}
