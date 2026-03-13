//! Prompt template formatting — mirrors hush-providers/hush/providers/ops/prompt.py.
//!
//! Supports three template formats:
//! 1. String: "Hello {name}" → [{"role": "user", "content": "Hello World"}]
//! 2. Dict: {"system": "...", "user": "..."} → system + user messages
//! 3. List: Full messages array (pass-through with variable substitution)
//!
//! Also handles conversation_history insertion and tool_results appending.
//! This op is pure CPU — no HTTP calls, no auth needed.

use regex::Regex;
use serde_json::{json, Value};

use crate::http::{ProviderError, ProviderResult};

/// Execute a prompt template formatting op.
///
/// Inputs: {"template": str|dict|list, "conversation_history": list, "tool_results": list, ...vars}
/// Outputs: {"messages": [{"role": str, "content": str}, ...]}
pub fn execute(inputs: Value) -> ProviderResult<Value> {
    let obj = inputs.as_object().ok_or_else(|| ProviderError {
        message: "Prompt inputs must be a dict".to_string(),
        status_code: None,
        error_code: None,
    })?;

    // Extract reserved keys
    let template = obj.get("template").cloned().unwrap_or(Value::Null);
    let conversation_history = obj
        .get("conversation_history")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let tool_results = obj
        .get("tool_results")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    // Remaining keys are template variables
    let mut vars = serde_json::Map::new();
    for (k, v) in obj {
        if k != "template" && k != "conversation_history" && k != "tool_results" {
            vars.insert(k.clone(), v.clone());
        }
    }

    // Format template to messages
    let mut messages = template_to_messages(&template, &vars)?;

    // Insert conversation history before last user message
    if !conversation_history.is_empty() {
        let last_user_idx = messages
            .iter()
            .rposition(|m| m.get("role").and_then(|r| r.as_str()) == Some("user"))
            .unwrap_or(messages.len());
        // Insert history at that position (before last user message)
        for (i, msg) in conversation_history.into_iter().enumerate() {
            messages.insert(last_user_idx + i, msg);
        }
    }

    // Append tool results
    if !tool_results.is_empty() {
        messages.extend(tool_results);
    }

    Ok(json!({ "messages": messages }))
}

/// Convert a template to a messages array.
fn template_to_messages(
    template: &Value,
    vars: &serde_json::Map<String, Value>,
) -> ProviderResult<Vec<Value>> {
    match template {
        Value::String(s) => {
            // Simple string → single user message
            let formatted = format_string(s, vars)?;
            Ok(vec![json!({"role": "user", "content": formatted})])
        }
        Value::Object(dict) => {
            // Dict with role keys → messages
            let mut messages = Vec::new();

            // System message first
            if let Some(system) = dict.get("system") {
                let content = format_value(system, vars)?;
                if let Some(s) = content.as_str() {
                    if !s.is_empty() {
                        messages.push(json!({"role": "system", "content": s}));
                    }
                }
            }

            // User message
            if let Some(user) = dict.get("user") {
                let content = format_value(user, vars)?;
                messages.push(json!({"role": "user", "content": content}));
            }

            // Assistant message (for few-shot or continuation)
            if let Some(assistant) = dict.get("assistant") {
                let content = format_value(assistant, vars)?;
                messages.push(json!({"role": "assistant", "content": content}));
            }

            Ok(messages)
        }
        Value::Array(arr) => {
            // Full messages array — format each message's content
            let mut messages = Vec::new();
            for msg in arr {
                let formatted = format_value(msg, vars)?;
                messages.push(formatted);
            }
            Ok(messages)
        }
        Value::Null => {
            // No template — return empty messages
            Ok(Vec::new())
        }
        _ => Err(ProviderError {
            message: format!(
                "Unsupported template type: expected str, dict, or list, got {:?}",
                template
            ),
            status_code: None,
            error_code: None,
        }),
    }
}

/// Format a string with {key} substitution from vars.
///
/// Raises an error if any `{var}` placeholders remain after substitution,
/// matching Python's PromptError for missing template variables.
fn format_string(
    template: &str,
    vars: &serde_json::Map<String, Value>,
) -> ProviderResult<String> {
    let mut result = template.to_string();
    for (key, value) in vars {
        let placeholder = format!("{{{}}}", key);
        let replacement = value_to_string(value);
        result = result.replace(&placeholder, &replacement);
    }

    // Detect remaining unreplaced {var} placeholders
    let re = Regex::new(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}").unwrap();
    let missing: Vec<String> = re
        .captures_iter(&result)
        .map(|c| c[1].to_string())
        .collect();

    if !missing.is_empty() {
        return Err(ProviderError {
            message: format!(
                "Missing template variable(s): {}. Template: {:?}",
                missing.join(", "),
                template,
            ),
            status_code: None,
            error_code: None,
        });
    }

    Ok(result)
}

/// Recursively format a JSON value, substituting {key} in strings.
fn format_value(
    value: &Value,
    vars: &serde_json::Map<String, Value>,
) -> ProviderResult<Value> {
    match value {
        Value::String(s) => {
            let formatted = format_string(s, vars)?;
            Ok(Value::String(formatted))
        }
        Value::Object(obj) => {
            let mut new_obj = serde_json::Map::new();
            for (k, v) in obj {
                new_obj.insert(k.clone(), format_value(v, vars)?);
            }
            Ok(Value::Object(new_obj))
        }
        Value::Array(arr) => {
            let new_arr: Vec<Value> = arr
                .iter()
                .map(|v| format_value(v, vars))
                .collect::<ProviderResult<_>>()?;
            Ok(Value::Array(new_arr))
        }
        _ => Ok(value.clone()),
    }
}

/// Convert a JSON value to its string representation for template substitution.
fn value_to_string(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) => n.to_string(),
        _ => value.to_string(), // Arrays/objects → JSON string
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // =========================================================================
    // String template tests
    // =========================================================================

    #[test]
    fn test_string_template_basic() {
        let result = execute(json!({
            "template": "Hello {name}",
            "name": "World"
        }))
        .unwrap();

        let messages = result["messages"].as_array().unwrap();
        assert_eq!(messages.len(), 1);
        assert_eq!(messages[0]["role"], "user");
        assert_eq!(messages[0]["content"], "Hello World");
    }

    #[test]
    fn test_string_template_multiple_vars() {
        let result = execute(json!({
            "template": "{greeting}, {name}! You are {age} years old.",
            "greeting": "Hi",
            "name": "Alice",
            "age": 30
        }))
        .unwrap();

        let content = result["messages"][0]["content"].as_str().unwrap();
        assert_eq!(content, "Hi, Alice! You are 30 years old.");
    }

    #[test]
    fn test_string_template_no_vars() {
        let result = execute(json!({
            "template": "No variables here"
        }))
        .unwrap();

        assert_eq!(result["messages"][0]["content"], "No variables here");
    }

    #[test]
    fn test_string_template_unused_var() {
        // Vars that don't appear in template are ignored
        let result = execute(json!({
            "template": "Hello {name}",
            "name": "World",
            "unused": "value"
        }))
        .unwrap();

        assert_eq!(result["messages"][0]["content"], "Hello World");
    }

    #[test]
    fn test_string_template_missing_var() {
        // Missing vars now raise an error (matches Python PromptError)
        let result = execute(json!({
            "template": "Hello {name}"
        }));

        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(err.message.contains("Missing template variable"));
        assert!(err.message.contains("name"));
    }

    // =========================================================================
    // Dict template tests
    // =========================================================================

    #[test]
    fn test_dict_template_system_user() {
        let result = execute(json!({
            "template": {
                "system": "You are a {role}.",
                "user": "Tell me about {topic}."
            },
            "role": "helpful assistant",
            "topic": "Rust"
        }))
        .unwrap();

        let messages = result["messages"].as_array().unwrap();
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0]["role"], "system");
        assert_eq!(messages[0]["content"], "You are a helpful assistant.");
        assert_eq!(messages[1]["role"], "user");
        assert_eq!(messages[1]["content"], "Tell me about Rust.");
    }

    #[test]
    fn test_dict_template_all_roles() {
        let result = execute(json!({
            "template": {
                "system": "System prompt",
                "user": "User message",
                "assistant": "Assistant response"
            }
        }))
        .unwrap();

        let messages = result["messages"].as_array().unwrap();
        assert_eq!(messages.len(), 3);
        assert_eq!(messages[0]["role"], "system");
        assert_eq!(messages[1]["role"], "user");
        assert_eq!(messages[2]["role"], "assistant");
    }

    #[test]
    fn test_dict_template_user_only() {
        let result = execute(json!({
            "template": { "user": "Hello" }
        }))
        .unwrap();

        let messages = result["messages"].as_array().unwrap();
        assert_eq!(messages.len(), 1);
        assert_eq!(messages[0]["role"], "user");
    }

    #[test]
    fn test_dict_template_empty_system_skipped() {
        let result = execute(json!({
            "template": { "system": "", "user": "Hello" }
        }))
        .unwrap();

        let messages = result["messages"].as_array().unwrap();
        // Empty system message should be skipped
        assert_eq!(messages.len(), 1);
        assert_eq!(messages[0]["role"], "user");
    }

    // =========================================================================
    // List template tests
    // =========================================================================

    #[test]
    fn test_list_template_passthrough() {
        let result = execute(json!({
            "template": [
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "Hello {name}"}
            ],
            "name": "World"
        }))
        .unwrap();

        let messages = result["messages"].as_array().unwrap();
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0]["content"], "Be helpful.");
        assert_eq!(messages[1]["content"], "Hello World");
    }

    // =========================================================================
    // Null / missing template
    // =========================================================================

    #[test]
    fn test_null_template() {
        let result = execute(json!({})).unwrap();
        let messages = result["messages"].as_array().unwrap();
        assert!(messages.is_empty());
    }

    // =========================================================================
    // Conversation history tests
    // =========================================================================

    #[test]
    fn test_conversation_history_inserted_before_last_user() {
        let result = execute(json!({
            "template": {
                "system": "You are helpful.",
                "user": "Current question"
            },
            "conversation_history": [
                {"role": "user", "content": "Previous question"},
                {"role": "assistant", "content": "Previous answer"}
            ]
        }))
        .unwrap();

        let messages = result["messages"].as_array().unwrap();
        // system, then history (user + assistant), then current user
        assert_eq!(messages.len(), 4);
        assert_eq!(messages[0]["role"], "system");
        assert_eq!(messages[1]["content"], "Previous question");
        assert_eq!(messages[2]["content"], "Previous answer");
        assert_eq!(messages[3]["content"], "Current question");
    }

    #[test]
    fn test_empty_conversation_history() {
        let result = execute(json!({
            "template": "Hello",
            "conversation_history": []
        }))
        .unwrap();

        let messages = result["messages"].as_array().unwrap();
        assert_eq!(messages.len(), 1);
    }

    // =========================================================================
    // Tool results tests
    // =========================================================================

    #[test]
    fn test_tool_results_appended() {
        let result = execute(json!({
            "template": "Hello",
            "tool_results": [
                {"role": "tool", "content": "Tool output", "tool_call_id": "t1"}
            ]
        }))
        .unwrap();

        let messages = result["messages"].as_array().unwrap();
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0]["role"], "user");
        assert_eq!(messages[1]["role"], "tool");
    }

    // =========================================================================
    // Value conversion tests
    // =========================================================================

    #[test]
    fn test_value_to_string_types() {
        assert_eq!(value_to_string(&json!("hello")), "hello");
        assert_eq!(value_to_string(&json!(42)), "42");
        assert_eq!(value_to_string(&json!(3.14)), "3.14");
        assert_eq!(value_to_string(&json!(true)), "true");
        assert_eq!(value_to_string(&json!(false)), "false");
        assert_eq!(value_to_string(&json!(null)), "");
    }

    #[test]
    fn test_value_to_string_complex() {
        // Arrays/objects get JSON-serialized
        let arr = value_to_string(&json!([1, 2, 3]));
        assert!(arr.contains("1"));
        assert!(arr.contains("2"));
        assert!(arr.contains("3"));
    }

    // =========================================================================
    // Error cases
    // =========================================================================

    #[test]
    fn test_invalid_input_not_dict() {
        let result = execute(json!("not a dict"));
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .message
            .contains("Prompt inputs must be a dict"));
    }

    #[test]
    fn test_unsupported_template_type() {
        let result = execute(json!({ "template": 42 }));
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .message
            .contains("Unsupported template type"));
    }

    // =========================================================================
    // Combined: history + tool results
    // =========================================================================

    #[test]
    fn test_history_and_tool_results_combined() {
        let result = execute(json!({
            "template": { "system": "You are an assistant.", "user": "Help me." },
            "conversation_history": [
                {"role": "user", "content": "Prev q"},
                {"role": "assistant", "content": "Prev a"}
            ],
            "tool_results": [
                {"role": "tool", "content": "calc result", "tool_call_id": "t1"}
            ]
        }))
        .unwrap();

        let messages = result["messages"].as_array().unwrap();
        // system + 2 history + user + 1 tool = 5
        assert_eq!(messages.len(), 5);
        assert_eq!(messages[0]["role"], "system");
        assert_eq!(messages[1]["content"], "Prev q");
        assert_eq!(messages[2]["content"], "Prev a");
        assert_eq!(messages[3]["content"], "Help me.");
        assert_eq!(messages[4]["role"], "tool");
    }
}
