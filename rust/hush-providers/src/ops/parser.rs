//! ParserOp — extract structured data from text (JSON, XML, YAML).
//!
//! Mirrors hush-core/hush/core/ops/transform/parser_op.py.
//! Supports code fence stripping, dot-path extraction, and type conversion.

use serde_json::{json, Map, Value};

use crate::http::{ProviderError, ProviderResult};

// =============================================================================
// Extract field schema
// =============================================================================

/// A field to extract: "user.address.city: str" → chain_path=["user","address","city"], type_hint="str"
struct ExtractField {
    output_key: String,
    chain_path: Vec<String>,
    type_hint: String,
}

impl ExtractField {
    fn from_string(schema_str: &str) -> Self {
        let (chain_text, type_hint) = if let Some(idx) = schema_str.find(':') {
            (schema_str[..idx].trim(), schema_str[idx + 1..].trim())
        } else {
            (schema_str.trim(), "Any")
        };

        let chain_path: Vec<String> = chain_text.split('.').map(|s| s.to_string()).collect();
        let output_key = chain_path.last().cloned().unwrap_or_default();

        Self {
            output_key,
            chain_path,
            type_hint: type_hint.to_string(),
        }
    }
}

// =============================================================================
// Code fence stripping
// =============================================================================

/// Strip markdown code fences (```...```) from text.
fn strip_code_fences(text: &str) -> String {
    let trimmed = text.trim();
    if trimmed.starts_with("```") {
        let lines: Vec<&str> = trimmed.lines().collect();
        if lines.len() > 2 {
            return lines[1..lines.len() - 1].join("\n");
        }
    }
    trimmed.to_string()
}

// =============================================================================
// Parsers
// =============================================================================

fn parse_json(text: &str) -> ProviderResult<Value> {
    let clean = strip_code_fences(text);
    serde_json::from_str(&clean).map_err(|e| ProviderError {
        message: format!("Failed to parse JSON: {}", e),
        status_code: None,
        error_code: None,
    })
}

fn parse_yaml(text: &str) -> ProviderResult<Value> {
    let clean = strip_code_fences(text);
    serde_yaml::from_str::<Value>(&clean).map_err(|e| ProviderError {
        message: format!("Failed to parse YAML: {}", e),
        status_code: None,
        error_code: None,
    })
}

/// Parse XML text into a JSON Value, matching Python's xml_to_dict behavior.
///
/// Handles:
/// - Single root element: `<root>...</root>` → `{"root": {...}}`
/// - Multiple root elements: `<a>1</a><b>2</b>` → wraps in `<root>`, flattens
/// - Duplicate tags → aggregated into lists
/// - Nested elements → recursive dicts
/// - Leaf elements → text content as string
fn parse_xml(text: &str) -> ProviderResult<Value> {
    let clean = strip_code_fences(text);

    // Try parsing as-is first
    match parse_xml_root(&clean) {
        Ok(val) => Ok(val),
        Err(_) => {
            // Multiple root elements — wrap in <root> and flatten
            let wrapped = format!("<root>{}</root>", clean);
            match parse_xml_inner(&wrapped) {
                Ok(val) => Ok(val),
                Err(e) => Err(ProviderError {
                    message: format!("Failed to parse XML: {}", e),
                    status_code: None,
                    error_code: None,
                }),
            }
        }
    }
}

/// Parse XML with a single root element → {"tag": content}
fn parse_xml_root(text: &str) -> Result<Value, String> {
    let reader = quick_xml::Reader::from_str(text);
    let (tag, children) = parse_xml_element_from_reader(reader, true)?;

    Ok(json!({ tag: children }))
}

/// Parse XML inner content (after wrapping in <root>) and flatten the root level.
fn parse_xml_inner(text: &str) -> Result<Value, String> {
    let reader = quick_xml::Reader::from_str(text);
    let (_tag, children) = parse_xml_element_from_reader(reader, false)?;
    Ok(children)
}

/// Parse a single XML element and all its children recursively using quick-xml.
///
/// `single_root_only`: if true, returns Err when multiple root elements are found
/// (used by parse_xml_root to detect multi-root and fall through to wrapped mode).
fn parse_xml_element_from_reader(
    mut reader: quick_xml::Reader<&[u8]>,
    single_root_only: bool,
) -> Result<(String, Value), String> {
    use quick_xml::events::Event;

    let mut buf = Vec::new();
    let mut root_tag = String::new();
    let mut root_result = Value::Null;
    let mut found_root = false;

    // Stack for nested parsing: (tag_name, accumulated_map, text_content)
    let mut stack: Vec<(String, Map<String, Value>, String)> = Vec::new();

    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) => {
                let tag = String::from_utf8_lossy(e.name().as_ref()).to_string();
                // Detect multiple root elements
                if stack.is_empty() && found_root && single_root_only {
                    return Err("Multiple root elements".to_string());
                }
                stack.push((tag, Map::new(), String::new()));
            }
            Ok(Event::End(_)) => {
                if let Some((tag, children, text)) = stack.pop() {
                    let value = if children.is_empty() {
                        // Leaf element — return text content
                        Value::String(text)
                    } else {
                        // Branch element — return dict of children
                        Value::Object(children)
                    };

                    if let Some(parent) = stack.last_mut() {
                        // Insert into parent, handling duplicate tags → list
                        let entry = parent.1.entry(tag).or_insert(Value::Null);
                        if *entry == Value::Null {
                            *entry = value;
                        } else if entry.is_array() {
                            entry.as_array_mut().unwrap().push(value);
                        } else {
                            let prev = entry.clone();
                            *entry = json!([prev, value]);
                        }
                    } else {
                        // Root element
                        root_tag = tag;
                        root_result = value;
                        found_root = true;
                    }
                }
            }
            Ok(Event::Text(ref e)) => {
                let text = e.unescape().unwrap_or_default().to_string();
                if let Some(current) = stack.last_mut() {
                    current.2.push_str(&text);
                }
            }
            Ok(Event::Empty(ref e)) => {
                // Self-closing tag like <br/>
                let tag = String::from_utf8_lossy(e.name().as_ref()).to_string();
                if let Some(parent) = stack.last_mut() {
                    let entry = parent.1.entry(tag).or_insert(Value::Null);
                    if *entry == Value::Null {
                        *entry = Value::String(String::new());
                    }
                }
            }
            Ok(Event::Eof) => break,
            Err(e) => return Err(format!("XML parse error: {}", e)),
            _ => {}
        }
        buf.clear();
    }

    Ok((root_tag, root_result))
}

// =============================================================================
// Extraction and type conversion
// =============================================================================

/// Extract a value from nested data using a dot-separated path.
fn extract_by_path(data: &Value, chain_path: &[String]) -> Value {
    let mut current = data;
    for key in chain_path {
        match current.get(key.as_str()) {
            Some(v) => current = v,
            None => return Value::Null,
        }
    }
    current.clone()
}

/// Convert a JSON value to the specified type hint, matching Python's behavior.
fn convert_type(value: &Value, type_hint: &str) -> Value {
    let hint = type_hint.to_lowercase();
    let hint = hint.trim();

    match hint {
        "bool" | "boolean" => match value {
            Value::Null => Value::Null,
            Value::Bool(b) => Value::Bool(*b),
            Value::String(s) => {
                let lower = s.to_lowercase();
                let lower = lower.trim();
                match lower {
                    "true" | "1" | "yes" => Value::Bool(true),
                    "false" | "0" | "no" | "" => Value::Bool(false),
                    _ => Value::Bool(!s.is_empty()),
                }
            }
            _ => value.clone(),
        },
        "int" => match value {
            Value::Null => Value::Null,
            Value::Number(n) => {
                if let Some(i) = n.as_i64() {
                    json!(i)
                } else if let Some(f) = n.as_f64() {
                    json!(f as i64)
                } else {
                    value.clone()
                }
            }
            Value::String(s) => match s.trim().parse::<i64>() {
                Ok(i) => json!(i),
                Err(_) => value.clone(),
            },
            _ => value.clone(),
        },
        "float" | "number" => match value {
            Value::Null => Value::Null,
            Value::Number(n) => {
                if let Some(f) = n.as_f64() {
                    json!(f)
                } else {
                    value.clone()
                }
            }
            Value::String(s) => match s.trim().parse::<f64>() {
                Ok(f) => json!(f),
                Err(_) => value.clone(),
            },
            _ => value.clone(),
        },
        "str" | "string" => match value {
            Value::Null => json!(""),
            Value::String(s) => Value::String(s.clone()),
            other => json!(other.to_string()),
        },
        // dict, list, any, unknown → pass through
        _ => value.clone(),
    }
}

// =============================================================================
// Main entry point
// =============================================================================

/// Execute parser op.
///
/// Inputs: `{"text": str, "parser_format": str, "parser_extract": [str, ...]}`
/// Outputs: `{field1: val1, field2: val2, ...}` — one key per extracted field.
pub fn execute(inputs: Value) -> ProviderResult<Value> {
    let obj = inputs.as_object().ok_or_else(|| ProviderError {
        message: "Parser inputs must be a dict".to_string(),
        status_code: None,
        error_code: None,
    })?;

    let text = obj
        .get("text")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    if text.is_empty() {
        return Ok(json!({}));
    }

    let format = obj
        .get("parser_format")
        .and_then(|v| v.as_str())
        .unwrap_or("xml");

    let extract = obj
        .get("parser_extract")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    // Parse text by format
    let parsed = match format {
        "json" => parse_json(text)?,
        "yaml" => parse_yaml(text)?,
        _ => parse_xml(text)?,
    };

    // Extract fields
    let mut result = Map::new();
    for schema_val in &extract {
        let schema_str = schema_val.as_str().unwrap_or("");
        if schema_str.is_empty() {
            continue;
        }
        let field = ExtractField::from_string(schema_str);
        let raw = extract_by_path(&parsed, &field.chain_path);
        let converted = convert_type(&raw, &field.type_hint);
        result.insert(field.output_key, converted);
    }

    Ok(Value::Object(result))
}

// =============================================================================
// Tests
// =============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // --- Code fence stripping ---

    #[test]
    fn test_strip_code_fences_json() {
        let input = "```json\n{\"a\": 1}\n```";
        assert_eq!(strip_code_fences(input), "{\"a\": 1}");
    }

    #[test]
    fn test_strip_code_fences_no_fence() {
        assert_eq!(strip_code_fences("{\"a\": 1}"), "{\"a\": 1}");
    }

    #[test]
    fn test_strip_code_fences_whitespace() {
        let input = "  ```xml\n<a>1</a>\n```  ";
        assert_eq!(strip_code_fences(input), "<a>1</a>");
    }

    // --- JSON parsing ---

    #[test]
    fn test_parse_json_basic() {
        let result = parse_json(r#"{"name": "Alice", "age": 30}"#).unwrap();
        assert_eq!(result["name"], "Alice");
        assert_eq!(result["age"], 30);
    }

    #[test]
    fn test_parse_json_with_fence() {
        let result = parse_json("```json\n{\"x\": 1}\n```").unwrap();
        assert_eq!(result["x"], 1);
    }

    #[test]
    fn test_parse_json_invalid() {
        assert!(parse_json("not json").is_err());
    }

    // --- XML parsing ---

    #[test]
    fn test_parse_xml_single_root() {
        let result = parse_xml("<data><name>Alice</name><age>30</age></data>").unwrap();
        assert_eq!(result["data"]["name"], "Alice");
        assert_eq!(result["data"]["age"], "30");
    }

    #[test]
    fn test_parse_xml_multiple_roots() {
        let result = parse_xml("<name>Alice</name><age>30</age>").unwrap();
        assert_eq!(result["name"], "Alice");
        assert_eq!(result["age"], "30");
    }

    #[test]
    fn test_parse_xml_nested() {
        let result =
            parse_xml("<user><address><city>NYC</city></address></user>").unwrap();
        assert_eq!(result["user"]["address"]["city"], "NYC");
    }

    #[test]
    fn test_parse_xml_duplicate_tags() {
        let result = parse_xml("<items><item>a</item><item>b</item></items>").unwrap();
        let items = result["items"]["item"].as_array().unwrap();
        assert_eq!(items.len(), 2);
        assert_eq!(items[0], "a");
        assert_eq!(items[1], "b");
    }

    #[test]
    fn test_parse_xml_with_fence() {
        let result = parse_xml("```xml\n<a>1</a><b>2</b>\n```").unwrap();
        assert_eq!(result["a"], "1");
        assert_eq!(result["b"], "2");
    }

    #[test]
    fn test_parse_xml_leaf_root() {
        let result = parse_xml("<answer>42</answer>").unwrap();
        assert_eq!(result["answer"], "42");
    }

    // --- YAML parsing ---

    #[test]
    fn test_parse_yaml_basic() {
        let result = parse_yaml("name: Alice\nage: 30").unwrap();
        assert_eq!(result["name"], "Alice");
        assert_eq!(result["age"], 30);
    }

    #[test]
    fn test_parse_yaml_with_fence() {
        let result = parse_yaml("```yaml\nx: 1\ny: 2\n```").unwrap();
        assert_eq!(result["x"], 1);
        assert_eq!(result["y"], 2);
    }

    // --- Dot-path extraction ---

    #[test]
    fn test_extract_simple() {
        let data = json!({"name": "Alice"});
        assert_eq!(extract_by_path(&data, &["name".into()]), json!("Alice"));
    }

    #[test]
    fn test_extract_nested() {
        let data = json!({"user": {"address": {"city": "NYC"}}});
        let path = vec!["user".into(), "address".into(), "city".into()];
        assert_eq!(extract_by_path(&data, &path), json!("NYC"));
    }

    #[test]
    fn test_extract_missing() {
        let data = json!({"a": 1});
        assert_eq!(extract_by_path(&data, &["b".into()]), Value::Null);
    }

    // --- Type conversion ---

    #[test]
    fn test_convert_bool_from_string() {
        assert_eq!(convert_type(&json!("true"), "bool"), json!(true));
        assert_eq!(convert_type(&json!("false"), "bool"), json!(false));
        assert_eq!(convert_type(&json!("1"), "bool"), json!(true));
        assert_eq!(convert_type(&json!("0"), "bool"), json!(false));
        assert_eq!(convert_type(&json!("yes"), "bool"), json!(true));
        assert_eq!(convert_type(&json!("no"), "bool"), json!(false));
    }

    #[test]
    fn test_convert_bool_null() {
        assert_eq!(convert_type(&Value::Null, "bool"), Value::Null);
    }

    #[test]
    fn test_convert_int() {
        assert_eq!(convert_type(&json!("42"), "int"), json!(42));
        assert_eq!(convert_type(&json!(3.7), "int"), json!(3));
        assert_eq!(convert_type(&Value::Null, "int"), Value::Null);
    }

    #[test]
    fn test_convert_float() {
        assert_eq!(convert_type(&json!("3.14"), "float"), json!(3.14));
        assert_eq!(convert_type(&json!(42), "float"), json!(42.0));
        assert_eq!(convert_type(&Value::Null, "float"), Value::Null);
    }

    #[test]
    fn test_convert_str() {
        assert_eq!(convert_type(&json!(42), "str"), json!("42"));
        assert_eq!(convert_type(&Value::Null, "str"), json!(""));
    }

    #[test]
    fn test_convert_any_passthrough() {
        let val = json!({"a": 1});
        assert_eq!(convert_type(&val, "dict"), val);
        assert_eq!(convert_type(&val, "Any"), val);
    }

    // --- ExtractField ---

    #[test]
    fn test_extract_field_simple() {
        let f = ExtractField::from_string("name: str");
        assert_eq!(f.output_key, "name");
        assert_eq!(f.chain_path, vec!["name"]);
        assert_eq!(f.type_hint, "str");
    }

    #[test]
    fn test_extract_field_nested() {
        let f = ExtractField::from_string("user.address.city: str");
        assert_eq!(f.output_key, "city");
        assert_eq!(f.chain_path, vec!["user", "address", "city"]);
        assert_eq!(f.type_hint, "str");
    }

    #[test]
    fn test_extract_field_no_type() {
        let f = ExtractField::from_string("field");
        assert_eq!(f.output_key, "field");
        assert_eq!(f.type_hint, "Any");
    }

    // --- Full execute ---

    #[test]
    fn test_execute_json() {
        let result = execute(json!({
            "text": r#"{"name": "Alice", "age": 30}"#,
            "parser_format": "json",
            "parser_extract": ["name: str", "age: int"],
        }))
        .unwrap();
        assert_eq!(result["name"], "Alice");
        assert_eq!(result["age"], 30);
    }

    #[test]
    fn test_execute_xml() {
        let result = execute(json!({
            "text": "<name>Alice</name><is_active>true</is_active>",
            "parser_format": "xml",
            "parser_extract": ["name: str", "is_active: bool"],
        }))
        .unwrap();
        assert_eq!(result["name"], "Alice");
        assert_eq!(result["is_active"], true);
    }

    #[test]
    fn test_execute_yaml() {
        let result = execute(json!({
            "text": "name: Alice\nscore: 95.5",
            "parser_format": "yaml",
            "parser_extract": ["name: str", "score: float"],
        }))
        .unwrap();
        assert_eq!(result["name"], "Alice");
        assert_eq!(result["score"], 95.5);
    }

    #[test]
    fn test_execute_empty_text() {
        let result = execute(json!({
            "text": "",
            "parser_format": "json",
            "parser_extract": ["name: str"],
        }))
        .unwrap();
        assert_eq!(result, json!({}));
    }

    #[test]
    fn test_execute_nested_extraction() {
        let result = execute(json!({
            "text": r#"{"user": {"name": "Alice", "address": {"city": "NYC"}}}"#,
            "parser_format": "json",
            "parser_extract": ["user.name: str", "user.address.city: str"],
        }))
        .unwrap();
        assert_eq!(result["name"], "Alice");
        assert_eq!(result["city"], "NYC");
    }
}
