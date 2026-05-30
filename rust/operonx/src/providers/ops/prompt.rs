//! `PromptOp` — builds chat messages from templates.
//!
//! Mirrors Python [`operonx/providers/ops/prompt.py`](../../../../../operonx/providers/ops/prompt.py).
//!
//! Accepts three template forms:
//!
//! - **String** — becomes a single user message (`"Hello {name}"`).
//! - **Dict** — `{"system": "...", "user": "..."}` with `{var}` placeholders.
//! - **List** — a pre-built messages array for multimodal / advanced prompts.
//!
//! All non-reserved input keys become template variables.

use serde_json::{json, Map, Value};

use crate::core::exceptions::{OpError, OperonError};

/// Execute the prompt op.
///
/// `inputs` contains the resolved input map. The function extracts
/// `template`, `conversation_history`, `tool_results`, and treats the rest
/// as template variables. Returns `{"messages": [...]}`.
pub async fn execute(inputs: Map<String, Value>) -> Result<Value, OperonError> {
    let mut inputs = inputs;
    let template = inputs.remove("template").unwrap_or(Value::Null);
    let history = inputs
        .remove("conversation_history")
        .unwrap_or_else(|| json!([]));
    let tools = inputs.remove("tool_results").unwrap_or_else(|| json!([]));

    // Remaining inputs become template variables.
    let vars = inputs;

    let mut messages = template_to_messages(&template, &vars)?;

    // Insert conversation history before the last user message (or append if
    // no user messages exist).
    if let Value::Array(history) = history {
        if !history.is_empty() {
            let last_user = messages.iter().rposition(|m| {
                m.as_object()
                    .and_then(|o| o.get("role"))
                    .and_then(|v| v.as_str())
                    == Some("user")
            });
            match last_user {
                Some(idx) => {
                    let tail: Vec<Value> = messages.split_off(idx);
                    messages.extend(history);
                    messages.extend(tail);
                }
                None => messages.extend(history),
            }
        }
    }

    // Append tool results.
    if let Value::Array(tools) = tools {
        messages.extend(tools);
    }

    let mut out = Map::new();
    out.insert("messages".into(), Value::Array(messages));
    Ok(Value::Object(out))
}

fn template_to_messages(
    template: &Value,
    vars: &Map<String, Value>,
) -> Result<Vec<Value>, OperonError> {
    match template {
        Value::Null => Ok(Vec::new()),
        // Form 1: Bare string → single user message.
        Value::String(s) => {
            let content = format_string(s, vars, template)?;
            Ok(vec![json!({"role": "user", "content": content})])
        }
        // Form 2: Dict with system/user keys, OR a raw message dict.
        Value::Object(obj) => {
            if obj.contains_key("system") || obj.contains_key("user") {
                let mut out = Vec::new();
                if let Some(sys) = obj.get("system") {
                    let content = format_value(sys, vars, template)?;
                    out.push(json!({"role": "system", "content": content}));
                }
                if let Some(user) = obj.get("user") {
                    let content = format_value(user, vars, template)?;
                    out.push(json!({"role": "user", "content": content}));
                }
                Ok(out)
            } else {
                let formatted = format_value(template, vars, template)?;
                Ok(vec![formatted])
            }
        }
        // Form 3: List → pre-built messages array.
        Value::Array(items) => items
            .iter()
            .map(|item| format_value(item, vars, template))
            .collect(),
        other => Err(OperonError::Op(OpError::prompt_msg(format!(
            "template must be null, string, object, or array — got {}",
            kind_name(other)
        )))),
    }
}

/// Recursively format `{var}` placeholders inside arbitrary JSON value.
fn format_value(
    value: &Value,
    vars: &Map<String, Value>,
    template: &Value,
) -> Result<Value, OperonError> {
    match value {
        Value::String(s) => format_string(s, vars, template).map(Value::String),
        Value::Array(arr) => arr
            .iter()
            .map(|v| format_value(v, vars, template))
            .collect::<Result<Vec<_>, _>>()
            .map(Value::Array),
        Value::Object(obj) => {
            let mut out = Map::new();
            for (k, v) in obj {
                out.insert(k.clone(), format_value(v, vars, template)?);
            }
            Ok(Value::Object(out))
        }
        other => Ok(other.clone()),
    }
}

/// Format a `{var}` placeholder string. Emits a diagnostic
/// [`OperonError::Op`] when any `{name}` fails to resolve.
fn format_string(
    s: &str,
    vars: &Map<String, Value>,
    template: &Value,
) -> Result<String, OperonError> {
    if !s.contains('{') {
        return Ok(s.to_string());
    }
    // Two-pass: scan for `{…}` segments, resolve each against `vars`.
    let mut out = String::with_capacity(s.len());
    let mut missing: Vec<String> = Vec::new();
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'{' {
            // Python's str.format_map semantics: `{{` is a literal `{`.
            if i + 1 < bytes.len() && bytes[i + 1] == b'{' {
                out.push('{');
                i += 2;
                continue;
            }
            let Some(end) = s[i + 1..].find('}') else {
                // Unterminated `{` — leave literal, matches Python's error
                // but we swallow for robustness.
                out.push_str(&s[i..]);
                break;
            };
            let name = &s[i + 1..i + 1 + end];
            let name = name.trim();
            if !is_identifier(name) {
                // Not a template variable (e.g. JSON snippet) — emit literal.
                out.push_str(&s[i..i + 2 + end]);
                i += 2 + end;
                continue;
            }
            match vars.get(name) {
                Some(v) => out.push_str(&render_scalar(v)),
                None => {
                    missing.push(name.to_string());
                }
            }
            i += 2 + end;
        } else if bytes[i] == b'}' && i + 1 < bytes.len() && bytes[i + 1] == b'}' {
            out.push('}');
            i += 2;
        } else {
            // Safe: we scan by bytes but only emit when we hit `{`/`}`.
            let ch_end = next_char_boundary(s, i);
            out.push_str(&s[i..ch_end]);
            i = ch_end;
        }
    }
    if !missing.is_empty() {
        return Err(OperonError::Op(OpError::Prompt {
            message: format!("missing template variable(s): {}", missing.join(", ")),
            template_type: "str".into(),
            template: template.to_string(),
            missing_vars: missing.clone(),
            original_error: None,
        }));
    }
    Ok(out)
}

fn render_scalar(v: &Value) -> String {
    match v {
        Value::Null => "None".to_string(),
        Value::Bool(b) => if *b { "True" } else { "False" }.to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

fn is_identifier(s: &str) -> bool {
    let mut chars = s.chars();
    match chars.next() {
        Some(c) if c == '_' || c.is_ascii_alphabetic() => (),
        _ => return false,
    }
    chars.all(|c| c == '_' || c.is_ascii_alphanumeric())
}

fn next_char_boundary(s: &str, i: usize) -> usize {
    let bytes = s.as_bytes();
    let mut j = i + 1;
    while j < bytes.len() && (bytes[j] & 0b1100_0000) == 0b1000_0000 {
        j += 1;
    }
    j
}

fn kind_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "null",
        Value::Bool(_) => "bool",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vars(pairs: &[(&str, Value)]) -> Map<String, Value> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.clone()))
            .collect()
    }

    #[tokio::test]
    async fn string_template_becomes_user_message() {
        let mut inputs = Map::new();
        inputs.insert("template".into(), json!("Hello {name}"));
        inputs.insert("name".into(), json!("world"));
        let out = execute(inputs).await.unwrap();
        assert_eq!(
            out,
            json!({"messages": [{"role": "user", "content": "Hello world"}]})
        );
    }

    #[tokio::test]
    async fn system_user_template() {
        let mut inputs = Map::new();
        inputs.insert(
            "template".into(),
            json!({"system": "You are {role}.", "user": "Answer {q}"}),
        );
        inputs.insert("role".into(), json!("helpful"));
        inputs.insert("q".into(), json!("why?"));
        let out = execute(inputs).await.unwrap();
        let msgs = out.get("messages").unwrap().as_array().unwrap();
        assert_eq!(msgs.len(), 2);
        assert_eq!(msgs[0]["role"], "system");
        assert_eq!(msgs[0]["content"], "You are helpful.");
        assert_eq!(msgs[1]["role"], "user");
        assert_eq!(msgs[1]["content"], "Answer why?");
    }

    #[tokio::test]
    async fn missing_var_errors() {
        let mut inputs = Map::new();
        inputs.insert("template".into(), json!("Hi {missing}"));
        let err = execute(inputs).await.unwrap_err();
        assert!(err.to_string().contains("missing template variable"));
    }

    #[tokio::test]
    async fn history_inserts_before_last_user() {
        let mut inputs = Map::new();
        inputs.insert("template".into(), json!({"user": "now: {q}"}));
        inputs.insert("q".into(), json!("status?"));
        inputs.insert(
            "conversation_history".into(),
            json!([
                {"role": "user", "content": "prev"},
                {"role": "assistant", "content": "ok"}
            ]),
        );
        let out = execute(inputs).await.unwrap();
        let msgs = out.get("messages").unwrap().as_array().unwrap();
        assert_eq!(msgs.len(), 3);
        assert_eq!(msgs[0]["content"], "prev");
        assert_eq!(msgs[1]["content"], "ok");
        assert_eq!(msgs[2]["content"], "now: status?");
    }

    #[tokio::test]
    async fn list_template_passthrough() {
        let mut inputs = Map::new();
        inputs.insert(
            "template".into(),
            json!([{"role": "system", "content": "fixed"}]),
        );
        let out = execute(inputs).await.unwrap();
        assert_eq!(
            out.get("messages").unwrap(),
            &json!([{"role": "system", "content": "fixed"}])
        );
    }

    #[test]
    fn format_string_handles_escaped_braces() {
        let vars = vars(&[("n", json!(5))]);
        let tpl = Value::Null;
        let out = format_string("{{literal}} n={n}", &vars, &tpl).unwrap();
        assert_eq!(out, "{literal} n=5");
    }
}
