//! `ParserOp` — extract structured data from JSON / XML / YAML text.
//!
//! Mirrors Python [`operonx/core/ops/transform/parser_op.py`](../../../../../operonx/core/ops/transform/parser_op.py).
//!
//! At op-runtime the executor reads four inputs off the resolved inputs map:
//!
//! | input        | type            | source                                   |
//! |--------------|-----------------|------------------------------------------|
//! | `text`       | string          | usually a ref to upstream LLM `content`  |
//! | `mode`       | string literal  | "json" / "xml" / "yaml" (default "xml")  |
//! | `schema`     | array literal   | list of `"a.b.c: type"` extract specs    |
//! | `validators` | object literal  | optional `{key: ["@DEFAULT", "v1", ...]}` |
//!
//! On success the executor returns `{<extracted_field>: <value>, ..., "error": null}`.
//! On failure it returns `{"error": "<msg>"}` so downstream ops can branch on it —
//! Python parity (see Python `_process` returning `{"error": ...}` instead of raising).

use async_trait::async_trait;
use quick_xml::events::Event;
use quick_xml::Reader;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::core::exceptions::OperonError;
use crate::core::ops::base::{BaseOp, OpContext, OpMeta};

/// Parser format.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ParserType {
    Json,
    Xml,
    Yaml,
}

impl ParserType {
    pub fn from_str_lossy(s: &str) -> Self {
        match s.to_ascii_lowercase().as_str() {
            "json" => ParserType::Json,
            "yaml" | "yml" => ParserType::Yaml,
            _ => ParserType::Xml, // matches Python default
        }
    }
}

/// One extracted field — Python's `ExtractField.from_string("a.b.c: type")`.
#[derive(Debug, Clone)]
pub struct ExtractField {
    /// Last segment of the dot-path — the key the parsed value lands at.
    pub output_key: String,
    /// `["a", "b", "c"]` for `"a.b.c: int"`.
    pub chain_path: Vec<String>,
    /// `"int"` for `"a.b.c: int"`; `"Any"` if no `: T` suffix was given.
    pub type_hint: String,
}

impl ExtractField {
    /// Parse `"a.b.c: int"` (or `"a.b.c"` → type_hint "Any").
    pub fn from_string(schema_str: &str) -> Self {
        let (chain_text, type_hint) = match schema_str.find(':') {
            Some(i) => (&schema_str[..i], schema_str[i + 1..].trim().to_string()),
            None => (schema_str, "Any".to_string()),
        };
        let chain_path: Vec<String> = chain_text
            .trim()
            .split('.')
            .map(|s| s.to_string())
            .collect();
        let output_key = chain_path
            .last()
            .cloned()
            .unwrap_or_else(|| chain_text.to_string());
        ExtractField {
            output_key,
            chain_path,
            type_hint,
        }
    }
}

/// `ParserOp` — parse input text into structured data.
///
/// The struct itself is just metadata; the actual execution happens via the
/// free-function [`execute`] called from the scheduler's dispatch table.
pub struct ParserOp {
    pub meta: OpMeta,
    pub parse_as: ParserType,
    pub extract_fields: Vec<ExtractField>,
}

#[async_trait]
impl BaseOp for ParserOp {
    fn meta(&self) -> &OpMeta {
        &self.meta
    }

    async fn exec_core(
        &self,
        inputs: Map<String, Value>,
        _ctx: &OpContext<'_>,
    ) -> Result<Option<Value>, OperonError> {
        let val = execute(inputs).await?;
        Ok(Some(val))
    }
}

// ── Dispatch entry point — called from task_scheduler::execute_op ──────

/// Execute a parser op given a resolved inputs map. Returns a `Value::Object`
/// containing the extracted fields + an `error` key (null on success).
pub async fn execute(inputs: Map<String, Value>) -> Result<Value, OperonError> {
    // Mirrors Python `_process(text, mode, schema, validators)`:
    // - Validator type check returns {error: ...} (not exception).
    // - Empty text returns {error: "Empty input text"}.
    // - Parse failure returns {error: "Parse error (<mode>): <msg>"}.
    // - Validators with `@DEFAULT` semantics fall back when value not in allowed list.
    let text = inputs
        .get("text")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let validators = inputs.get("validators").cloned();
    if let Some(v) = &validators {
        if !v.is_null() && !v.is_object() {
            return Ok(error_value(format!(
                "validators must be a dict, got {}: {:?}",
                json_type_name(v),
                v
            )));
        }
    }

    if text.is_empty() {
        return Ok(error_value("Empty input text".to_string()));
    }

    let mode = inputs
        .get("mode")
        .and_then(|v| v.as_str())
        .map(ParserType::from_str_lossy)
        .unwrap_or(ParserType::Xml);

    let parsed = match mode {
        ParserType::Json => parse_json(&text),
        ParserType::Xml => parse_xml(&text),
        ParserType::Yaml => parse_yaml(&text),
    };
    let parsed = match parsed {
        Ok(v) => v,
        Err(e) => {
            return Ok(error_value(format!(
                "Parse error ({}): {}",
                match mode {
                    ParserType::Json => "json",
                    ParserType::Xml => "xml",
                    ParserType::Yaml => "yaml",
                },
                e
            )))
        }
    };

    let fields = parse_extract_fields(inputs.get("schema"));

    let mut result = Map::new();
    for field in &fields {
        let raw = extract_value_by_path(&parsed, &field.chain_path);
        let converted = convert_type(raw, &field.type_hint);
        result.insert(field.output_key.clone(), converted);
    }

    // Validation pass — only when validators is a non-null object.
    if let Some(Value::Object(v_map)) = &validators {
        for (field_name, allowed) in v_map.iter() {
            let Some(allowed_arr) = allowed.as_array() else {
                continue;
            };
            let clean_values: Vec<Value> = allowed_arr
                .iter()
                .map(|v| match v.as_str() {
                    Some(s) if s.starts_with('@') => Value::String(s[1..].to_string()),
                    _ => v.clone(),
                })
                .collect();
            let default_value: Option<String> = allowed_arr.iter().find_map(|v| match v.as_str() {
                Some(s) if s.starts_with('@') => Some(s[1..].to_string()),
                _ => None,
            });
            let current = result.get(field_name);
            let pass = match current {
                Some(c) if !c.is_null() => clean_values.iter().any(|cv| values_equal(cv, c)),
                _ => false,
            };
            if !pass {
                if let Some(def) = default_value {
                    result.insert(field_name.clone(), Value::String(def));
                } else {
                    return Ok(error_value(format!(
                        "Validation failed: '{}' value '{}' not in {}",
                        field_name,
                        current.map(value_repr).unwrap_or_else(|| "None".into()),
                        list_repr(&clean_values)
                    )));
                }
            }
        }
    }

    result.insert("error".into(), Value::Null);
    Ok(Value::Object(result))
}

fn error_value(msg: String) -> Value {
    let mut m = Map::new();
    m.insert("error".into(), Value::String(msg));
    Value::Object(m)
}

fn json_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(_) => "number",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

fn parse_extract_fields(schema: Option<&Value>) -> Vec<ExtractField> {
    match schema.and_then(|v| v.as_array()) {
        Some(arr) => arr
            .iter()
            .filter_map(|v| v.as_str().map(ExtractField::from_string))
            .collect(),
        None => Vec::new(),
    }
}

fn extract_value_by_path(data: &Value, chain_path: &[String]) -> Value {
    let mut current = data.clone();
    for key in chain_path {
        match &current {
            Value::Object(m) => {
                if let Some(v) = m.get(key) {
                    current = v.clone();
                } else {
                    return Value::Null;
                }
            }
            _ => return Value::Null,
        }
    }
    current
}

/// Match Python `_convert_type` behavior on `Value`s.
fn convert_type(value: Value, type_hint: &str) -> Value {
    let hint = type_hint.trim().to_ascii_lowercase();
    match hint.as_str() {
        "bool" | "boolean" => match &value {
            Value::Null => Value::Null,
            Value::Bool(_) => value,
            Value::String(s) => {
                let l = s.trim().to_ascii_lowercase();
                if matches!(l.as_str(), "true" | "1" | "yes") {
                    Value::Bool(true)
                } else if matches!(l.as_str(), "false" | "0" | "no" | "") {
                    Value::Bool(false)
                } else {
                    Value::Bool(true) // matches Python `bool(value)` for non-empty
                }
            }
            other => Value::Bool(!is_falsy(other)),
        },
        _ if matches!(value, Value::Null) => Value::Null,
        "int" => match &value {
            Value::Number(n) => Value::Number(serde_json::Number::from(n.as_i64().unwrap_or(0))),
            Value::String(s) => s
                .parse::<i64>()
                .ok()
                .map(|n| Value::Number(n.into()))
                .unwrap_or(value),
            Value::Bool(b) => Value::Number(if *b { 1 } else { 0 }.into()),
            _ => value,
        },
        "float" | "number" => match &value {
            Value::Number(_) => value,
            Value::String(s) => s
                .parse::<f64>()
                .ok()
                .and_then(serde_json::Number::from_f64)
                .map(Value::Number)
                .unwrap_or(value),
            Value::Bool(b) => Value::Number(
                serde_json::Number::from_f64(if *b { 1.0 } else { 0.0 }).unwrap_or(0.into()),
            ),
            _ => value,
        },
        "str" | "string" => match value {
            Value::Null => Value::String(String::new()),
            Value::String(s) => Value::String(s.trim().to_string()),
            other => Value::String(value_repr(&other).trim().to_string()),
        },
        _ => value, // dict / list / Any / unknown — pass through
    }
}

fn is_falsy(v: &Value) -> bool {
    match v {
        Value::Null => true,
        Value::Bool(b) => !b,
        Value::Number(n) => n.as_f64().map(|f| f == 0.0).unwrap_or(false),
        Value::String(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        Value::Object(m) => m.is_empty(),
    }
}

fn values_equal(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::String(x), Value::String(y)) => x == y,
        (Value::Number(x), Value::Number(y)) => x
            .as_f64()
            .zip(y.as_f64())
            .map(|(a, b)| a == b)
            .unwrap_or(x == y),
        _ => a == b,
    }
}

fn value_repr(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

fn list_repr(items: &[Value]) -> String {
    let parts: Vec<String> = items
        .iter()
        .map(|v| match v {
            Value::String(s) => format!("'{}'", s),
            other => other.to_string(),
        })
        .collect();
    format!("[{}]", parts.join(", "))
}

// ── Format-specific parsers ────────────────────────────────────────────

fn strip_code_fences(text: &str) -> &str {
    let t = text.trim();
    if t.starts_with("```") {
        let mut lines: Vec<&str> = t.lines().collect();
        if lines.len() > 2 {
            // Drop the opening ```... and the closing ``` line.
            lines.remove(0);
            lines.pop();
            // Rejoining produces an owned String — return original for now and
            // handle in caller. Use the manual offset path instead.
            // Fall through to compute offsets:
        }
    }
    text
}

/// Parse JSON text. Strips code fences first to match Python's behavior.
fn parse_json(text: &str) -> Result<Value, String> {
    let cleaned = strip_fences_owned(text);
    serde_json::from_str(&cleaned).map_err(|e| e.to_string())
}

/// Parse YAML text. Strips code fences first.
fn parse_yaml(text: &str) -> Result<Value, String> {
    let cleaned = strip_fences_owned(text);
    let v: serde_yaml::Value = serde_yaml::from_str(&cleaned).map_err(|e| e.to_string())?;
    yaml_to_json(v).map_err(|e| e.to_string())
}

fn strip_fences_owned(text: &str) -> String {
    let t = text.trim();
    if t.starts_with("```") {
        let lines: Vec<&str> = t.lines().collect();
        if lines.len() > 2 {
            return lines[1..lines.len() - 1].join("\n");
        }
    }
    text.to_string()
}

fn yaml_to_json(value: serde_yaml::Value) -> Result<Value, String> {
    use serde_yaml::Value as Yv;
    match value {
        Yv::Null => Ok(Value::Null),
        Yv::Bool(b) => Ok(Value::Bool(b)),
        Yv::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(Value::Number(i.into()))
            } else if let Some(f) = n.as_f64() {
                Ok(serde_json::Number::from_f64(f)
                    .map(Value::Number)
                    .unwrap_or(Value::Null))
            } else {
                Ok(Value::Null)
            }
        }
        Yv::String(s) => Ok(Value::String(s)),
        Yv::Sequence(seq) => {
            let v: Result<Vec<_>, _> = seq.into_iter().map(yaml_to_json).collect();
            Ok(Value::Array(v?))
        }
        Yv::Mapping(map) => {
            let mut out = Map::new();
            for (k, v) in map {
                let key = match k {
                    Yv::String(s) => s,
                    other => yaml_to_json(other)?
                        .as_str()
                        .map(String::from)
                        .unwrap_or_else(|| "".into()),
                };
                out.insert(key, yaml_to_json(v)?);
            }
            Ok(Value::Object(out))
        }
        Yv::Tagged(t) => yaml_to_json(t.value),
    }
}

/// Parse XML text using quick-xml. Mirrors Python's `xml_to_dict` shape:
///
/// - Element with no children: `{"tag": "text"}`.
/// - Element with children: `{"tag": {<child_tags>}}`.
/// - Repeated children with same tag: collapse to a list.
/// - Multiple roots: wrap in `<root>`, flatten as if children of `<root>`.
fn parse_xml(text: &str) -> Result<Value, String> {
    let cleaned = strip_fences_owned(text);
    // First try as-is.
    if let Ok(v) = parse_xml_inner(&cleaned) {
        return Ok(v);
    }
    // Multiple roots → wrap.
    let wrapped = format!("<root>{}</root>", cleaned);
    parse_xml_inner(&wrapped).map(|v| match v {
        Value::Object(mut m) => {
            if let Some(Value::Object(inner)) = m.remove("root") {
                Value::Object(inner)
            } else {
                Value::Object(m)
            }
        }
        other => other,
    })
}

fn parse_xml_inner(text: &str) -> Result<Value, String> {
    let mut reader = Reader::from_str(text);
    reader.config_mut().trim_text(true);

    // Stack of (tag_name, accumulator). On end-tag we collapse the
    // accumulator into either a string (no children) or an object.
    enum Frame {
        Empty,
        Text(String),
        Children(Map<String, Value>),
    }
    let mut stack: Vec<(String, Frame)> = Vec::new();
    let mut roots: Vec<(String, Value)> = Vec::new();

    loop {
        match reader.read_event() {
            Ok(Event::Start(e)) => {
                let name = std::str::from_utf8(e.name().as_ref())
                    .map_err(|e| e.to_string())?
                    .to_string();
                stack.push((name, Frame::Empty));
            }
            Ok(Event::End(_)) => {
                let (tag, frame) = stack
                    .pop()
                    .ok_or_else(|| "unbalanced XML end tag".to_string())?;
                let value = match frame {
                    Frame::Empty => Value::Null,
                    Frame::Text(s) => Value::String(s),
                    Frame::Children(m) => Value::Object(m),
                };
                // Merge into parent's Children frame, collapsing repeats to lists.
                if let Some((_p_tag, p_frame)) = stack.last_mut() {
                    let m = match p_frame {
                        Frame::Children(m) => m,
                        _ => {
                            *p_frame = Frame::Children(Map::new());
                            match p_frame {
                                Frame::Children(m) => m,
                                _ => unreachable!(),
                            }
                        }
                    };
                    if let Some(existing) = m.remove(&tag) {
                        match existing {
                            Value::Array(mut arr) => {
                                arr.push(value);
                                m.insert(tag, Value::Array(arr));
                            }
                            other => {
                                m.insert(tag, Value::Array(vec![other, value]));
                            }
                        }
                    } else {
                        m.insert(tag, value);
                    }
                } else {
                    roots.push((tag, value));
                }
            }
            Ok(Event::Text(t)) => {
                let s = t.unescape().map_err(|e| e.to_string())?.into_owned();
                if let Some((_tag, frame)) = stack.last_mut() {
                    if matches!(frame, Frame::Empty) {
                        *frame = Frame::Text(s);
                    } else if let Frame::Text(existing) = frame {
                        existing.push_str(&s);
                    }
                }
            }
            Ok(Event::Empty(e)) => {
                let name = std::str::from_utf8(e.name().as_ref())
                    .map_err(|e| e.to_string())?
                    .to_string();
                // Self-closing → represent as Null. Merge into parent.
                if let Some((_p_tag, p_frame)) = stack.last_mut() {
                    let m = match p_frame {
                        Frame::Children(m) => m,
                        _ => {
                            *p_frame = Frame::Children(Map::new());
                            match p_frame {
                                Frame::Children(m) => m,
                                _ => unreachable!(),
                            }
                        }
                    };
                    m.insert(name, Value::Null);
                } else {
                    roots.push((name, Value::Null));
                }
            }
            Ok(Event::Eof) => break,
            Ok(_) => {} // skip CData / Comment / Decl / PI / DocType
            Err(e) => {
                return Err(format!(
                    "XML parse error at pos {}: {}",
                    reader.error_position(),
                    e
                ))
            }
        }
    }

    if !stack.is_empty() {
        return Err("XML unbalanced — missing closing tag".to_string());
    }

    if roots.is_empty() {
        return Err("XML empty document".to_string());
    }

    if roots.len() == 1 {
        let (root_tag, root_value) = roots.pop().unwrap();
        let mut m = Map::new();
        m.insert(root_tag, root_value);
        Ok(Value::Object(m))
    } else {
        // Multiple roots — return as an object with all of them flattened.
        // Caller's wrapping path also handles this.
        let mut m = Map::new();
        for (tag, value) in roots {
            m.insert(tag, value);
        }
        Ok(Value::Object(m))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn extract_field_parses_dot_path_with_type() {
        let f = ExtractField::from_string("user.address.city: str");
        assert_eq!(f.output_key, "city");
        assert_eq!(f.chain_path, vec!["user", "address", "city"]);
        assert_eq!(f.type_hint, "str");
    }

    #[test]
    fn extract_field_defaults_to_any_when_no_type_suffix() {
        let f = ExtractField::from_string("user.name");
        assert_eq!(f.output_key, "name");
        assert_eq!(f.type_hint, "Any");
    }

    #[test]
    fn parse_json_strips_code_fence() {
        let v = parse_json("```json\n{\"a\": 1}\n```").expect("parse");
        assert_eq!(v, json!({"a": 1}));
    }

    #[test]
    fn parse_json_handles_raw_text() {
        let v = parse_json(r#"{"a": 1}"#).expect("parse");
        assert_eq!(v, json!({"a": 1}));
    }

    #[test]
    fn parse_xml_single_root() {
        let v = parse_xml("<root><name>Alice</name></root>").expect("parse");
        assert_eq!(v, json!({"root": {"name": "Alice"}}));
    }

    #[test]
    fn parse_xml_multiple_roots_get_flattened() {
        let v = parse_xml("<a>1</a><b>2</b>").expect("parse");
        // Per Python parity: wrapped in <root>, then xml_to_dict flattens.
        assert_eq!(v, json!({"a": "1", "b": "2"}));
    }

    #[test]
    fn parse_xml_repeated_tag_becomes_list() {
        let v = parse_xml("<root><item>1</item><item>2</item></root>").expect("parse");
        assert_eq!(v, json!({"root": {"item": ["1", "2"]}}));
    }

    #[test]
    fn parse_yaml_basic() {
        let v = parse_yaml("a: 1\nb: foo").expect("parse");
        assert_eq!(v, json!({"a": 1, "b": "foo"}));
    }

    #[test]
    fn convert_type_bool_true_strings() {
        assert_eq!(convert_type(json!("true"), "bool"), json!(true));
        assert_eq!(convert_type(json!("yes"), "bool"), json!(true));
        assert_eq!(convert_type(json!("1"), "bool"), json!(true));
    }

    #[test]
    fn convert_type_bool_false_strings() {
        assert_eq!(convert_type(json!("false"), "bool"), json!(false));
        assert_eq!(convert_type(json!(""), "bool"), json!(false));
        assert_eq!(convert_type(json!("0"), "bool"), json!(false));
    }

    #[test]
    fn convert_type_int_str_to_number() {
        assert_eq!(convert_type(json!("42"), "int"), json!(42));
    }

    #[test]
    fn convert_type_str_strips_whitespace() {
        assert_eq!(convert_type(json!("  hello  "), "str"), json!("hello"));
    }

    #[test]
    fn extract_value_by_path_walks_dict() {
        let data = json!({"a": {"b": {"c": 42}}});
        let v = extract_value_by_path(&data, &["a".into(), "b".into(), "c".into()]);
        assert_eq!(v, json!(42));
    }

    #[test]
    fn extract_value_by_path_missing_returns_null() {
        let data = json!({"a": 1});
        let v = extract_value_by_path(&data, &["a".into(), "b".into()]);
        assert_eq!(v, Value::Null);
    }
}
