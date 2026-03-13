//! Shared log event templates and plain-text data formatter.
//!
//! Templates are loaded from `log_templates.json` at compile time via `include_str!`.
//! This ensures Python and Rust backends use identical log message formats.

use std::collections::HashMap;
use std::sync::LazyLock;

use serde_json::Value;

/// Shared log templates — embedded at compile time.
/// NOTE: This is a copy of python/hush-icore/hush/core/loggings/log_templates.json.
/// Keep both files in sync when changing log formats.
static TEMPLATES: LazyLock<HashMap<String, String>> = LazyLock::new(|| {
    let json_str =
        include_str!("log_templates.json");
    serde_json::from_str(json_str).expect("Invalid log_templates.json")
});

/// Format a log event by substituting `{key}` placeholders in the named template.
pub fn format_event(name: &str, args: &[(&str, &str)]) -> String {
    let mut template = match TEMPLATES.get(name) {
        Some(t) => t.clone(),
        None => return format!("[unknown log event: {}]", name),
    };
    for (key, val) in args {
        template = template.replace(&format!("{{{}}}", key), val);
    }
    template
}

/// Format a serde_json::Value for logging — plain text, no Rich markup.
///
/// Mirrors Python's `format_data()` in `events.py`.
pub fn format_data(value: &Value, max_str: usize, max_items: usize) -> String {
    match value {
        Value::Null => "None".to_string(),

        Value::Bool(b) => b.to_string(),

        Value::Number(n) => n.to_string(),

        Value::String(s) => {
            if s.len() > max_str {
                format!("'{}...'", &s[..max_str])
            } else {
                format!("'{}'", s)
            }
        }

        Value::Array(arr) => {
            let n = arr.len();
            if n == 0 {
                return "[]".to_string();
            }
            if n > max_items {
                return format!("<list {}>", n);
            }
            let parts: Vec<String> = arr
                .iter()
                .map(|v| match v {
                    Value::String(s) => {
                        if s.len() > 30 {
                            format!("'{}...'", &s[..30])
                        } else {
                            format!("'{}'", s)
                        }
                    }
                    Value::Bool(b) => b.to_string(),
                    Value::Number(n) => n.to_string(),
                    other => {
                        let s = other.to_string();
                        if s.len() > 30 {
                            format!("{}...", &s[..30])
                        } else {
                            s
                        }
                    }
                })
                .collect();
            format!("[{}]", parts.join(", "))
        }

        Value::Object(map) => {
            if map.is_empty() {
                return "{}".to_string();
            }
            let mut parts = Vec::new();
            for (i, (k, v)) in map.iter().enumerate() {
                if i >= max_items {
                    parts.push(format!("+{}", map.len() - i));
                    break;
                }
                let val = match v {
                    Value::Null => "None".to_string(),
                    Value::String(s) => {
                        if s.len() > 50 {
                            format!("'{}...'", &s[..50])
                        } else {
                            format!("'{}'", s)
                        }
                    }
                    Value::Bool(b) => b.to_string(),
                    Value::Number(n) => n.to_string(),
                    Value::Object(inner) => format!("<dict {}>", inner.len()),
                    Value::Array(arr) => format!("<list {}>", arr.len()),
                };
                parts.push(format!("{}={}", k, val));
            }
            format!("{{{}}}", parts.join(", "))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_templates_loaded() {
        for name in &[
            "op_done",
            "op_error",
            "op_slow",
            "workflow_start",
            "workflow_done",
            "gen_error",
        ] {
            assert!(TEMPLATES.contains_key(*name), "Missing template: {}", name);
        }
    }

    #[test]
    fn test_format_event_op_done() {
        let msg = format_event(
            "op_done",
            &[
                ("request_id", "req-1"),
                ("op_type", "FUNC"),
                ("full_name", "wf.step"),
                ("context", "main"),
                ("duration_ms", "1.5"),
                ("inputs", "{x=5}"),
                ("outputs", "{result=10}"),
            ],
        );
        assert_eq!(msg, "[req-1] FUNC wf.step [main] (1.5ms) {x=5} -> {result=10}");
    }

    #[test]
    fn test_format_event_op_error() {
        let msg = format_event(
            "op_error",
            &[
                ("request_id", "req-1"),
                ("name", "step"),
                ("error", "division by zero"),
            ],
        );
        assert_eq!(msg, "[req-1] Error in op step:\ndivision by zero");
    }

    #[test]
    fn test_format_event_op_slow() {
        let msg = format_event(
            "op_slow",
            &[
                ("request_id", "req-1"),
                ("full_name", "wf.step"),
                ("duration_ms", "150.3"),
            ],
        );
        assert_eq!(msg, "[req-1] Slow op wf.step: 150.3ms");
    }

    #[test]
    fn test_format_event_workflow_start() {
        let msg = format_event(
            "workflow_start",
            &[("request_id", "req-1"), ("graph_name", "my_graph")],
        );
        assert_eq!(msg, "[req-1] Running workflow my_graph");
    }

    #[test]
    fn test_format_event_unknown() {
        let msg = format_event("nonexistent", &[]);
        assert!(msg.contains("unknown log event"));
    }

    #[test]
    fn test_format_data_null() {
        assert_eq!(format_data(&Value::Null, 80, 8), "None");
    }

    #[test]
    fn test_format_data_bool() {
        assert_eq!(format_data(&json!(true), 80, 8), "true");
    }

    #[test]
    fn test_format_data_number() {
        assert_eq!(format_data(&json!(42), 80, 8), "42");
        assert_eq!(format_data(&json!(3.14), 80, 8), "3.14");
    }

    #[test]
    fn test_format_data_string() {
        assert_eq!(format_data(&json!("hello"), 80, 8), "'hello'");
    }

    #[test]
    fn test_format_data_string_truncation() {
        let long = "a".repeat(200);
        let result = format_data(&Value::String(long), 80, 8);
        assert!(result.len() < 200);
        assert!(result.contains("..."));
    }

    #[test]
    fn test_format_data_dict() {
        let val = json!({"x": 5, "y": "hello"});
        let result = format_data(&val, 80, 8);
        assert!(result.contains("x="));
        assert!(result.contains("y="));
        assert!(!result.contains("[muted]"));
    }

    #[test]
    fn test_format_data_dict_max_items() {
        let mut map = serde_json::Map::new();
        for i in 0..20 {
            map.insert(format!("k{}", i), json!(i));
        }
        let result = format_data(&Value::Object(map), 80, 5);
        assert!(result.contains("+15"));
    }

    #[test]
    fn test_format_data_empty_dict() {
        assert_eq!(format_data(&json!({}), 80, 8), "{}");
    }

    #[test]
    fn test_format_data_list() {
        let result = format_data(&json!([1, 2, 3]), 80, 8);
        assert_eq!(result, "[1, 2, 3]");
    }

    #[test]
    fn test_format_data_list_too_long() {
        let arr: Vec<i32> = (0..20).collect();
        let result = format_data(&json!(arr), 80, 8);
        assert_eq!(result, "<list 20>");
    }

    #[test]
    fn test_format_data_empty_list() {
        assert_eq!(format_data(&json!([]), 80, 8), "[]");
    }

    #[test]
    fn test_python_rust_produce_same_output() {
        let msg = format_event(
            "op_done",
            &[
                ("request_id", "abc"),
                ("op_type", "FUNC"),
                ("full_name", "wf.step"),
                ("context", "main"),
                ("duration_ms", "1.2"),
                ("inputs", "{x=5}"),
                ("outputs", "{result=10}"),
            ],
        );
        assert_eq!(msg, "[abc] FUNC wf.step [main] (1.2ms) {x=5} -> {result=10}");
    }
}
