//! Prompt template formatting — mirrors hush-providers/hush/providers/ops/prompt.py.
//!
//! Supports three template formats:
//! 1. String: "Hello {name}" → [{"role": "user", "content": "Hello World"}]
//! 2. Dict: {"system": "...", "user": "..."} → system + user messages
//! 3. List: Full messages array (pass-through with variable substitution)
//!
//! Also handles conversation_history insertion and tool_results appending.
//! This op is pure CPU — no HTTP calls, no auth needed.

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
