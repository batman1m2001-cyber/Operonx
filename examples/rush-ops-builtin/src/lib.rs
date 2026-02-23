//! rush-ops-builtin — built-in Rust ops for the Hush workflow engine.
//!
//! Contains 13 ops across 4 categories: core, string, JSON, math.
//! Compiled as a cdylib plugin loaded by rush-core at runtime.
//!
//! Usage in Python:
//! ```python
//! @op(rust="./examples/rush-ops-builtin::double")
//! def double(x: int):
//!     return {"result": x * 2}  # Python fallback
//! ```

use rush_ops_sdk::serde_json::{self, Value};
use rush_ops_sdk::export_ops;

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};

// =============================================================================
// Core ops
// =============================================================================

/// double: x → result = x * 2
/// Accepts both int and float inputs.
fn double(inputs: &Value) -> Value {
    if let Some(x) = inputs["x"].as_i64() {
        serde_json::json!({"result": x * 2})
    } else if let Some(x) = inputs["x"].as_f64() {
        serde_json::json!({"result": x * 2.0})
    } else {
        serde_json::json!({"error": "missing or invalid input 'x'"})
    }
}

/// add: a, b → result = a + b
/// Accepts both int and float inputs.
fn add(inputs: &Value) -> Value {
    match (inputs["a"].as_i64(), inputs["b"].as_i64()) {
        (Some(a), Some(b)) => serde_json::json!({"result": a + b}),
        _ => {
            let a = inputs["a"].as_f64().unwrap_or(0.0);
            let b = inputs["b"].as_f64().unwrap_or(0.0);
            serde_json::json!({"result": a + b})
        }
    }
}

/// hash_chain: data, iterations → hash (CPU-heavy)
fn hash_chain(inputs: &Value) -> Value {
    let data = inputs["data"].as_str().unwrap_or("");
    let iterations = inputs["iterations"].as_i64().unwrap_or(0);

    let mut current = data.to_string();
    for _ in 0..iterations {
        let mut hasher = DefaultHasher::new();
        current.hash(&mut hasher);
        current = format!("{:016x}", hasher.finish());
    }

    serde_json::json!({"hash": current})
}

// =============================================================================
// String ops
// =============================================================================

/// string_concat: parts (list of str) → result (str)
fn string_concat(inputs: &Value) -> Value {
    let parts = inputs["parts"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .map(|v| v.as_str().unwrap_or(""))
                .collect::<Vec<_>>()
                .join("")
        })
        .unwrap_or_default();
    serde_json::json!({"result": parts})
}

/// string_split: text, delimiter → parts (list of str)
fn string_split(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    let delimiter = inputs["delimiter"].as_str().unwrap_or("");
    let parts: Vec<&str> = text.split(delimiter).collect();
    serde_json::json!({"parts": parts})
}

/// string_template: template, vars → result (str)
/// Simple {key} substitution.
fn string_template(inputs: &Value) -> Value {
    let template = inputs["template"].as_str().unwrap_or("").to_string();
    let mut output = template;

    if let Some(vars) = inputs["vars"].as_object() {
        for (key, value) in vars {
            let v = match value {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            };
            output = output.replace(&format!("{{{}}}", key), &v);
        }
    }

    serde_json::json!({"result": output})
}

// =============================================================================
// JSON ops
// =============================================================================

/// json_parse: text → data (parsed JSON)
fn json_parse(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("null");
    match serde_json::from_str::<Value>(text) {
        Ok(data) => serde_json::json!({"data": data}),
        Err(e) => serde_json::json!({"error": format!("JSON parse error: {}", e)}),
    }
}

/// json_extract: data, path (dot-separated) → value
fn json_extract(inputs: &Value) -> Value {
    let path = inputs["path"].as_str().unwrap_or("");
    let mut current = &inputs["data"];

    for key in path.split('.') {
        current = match current {
            Value::Object(map) => map.get(key).unwrap_or(&Value::Null),
            Value::Array(arr) => {
                if let Ok(idx) = key.parse::<usize>() {
                    arr.get(idx).unwrap_or(&Value::Null)
                } else {
                    &Value::Null
                }
            }
            _ => &Value::Null,
        };
    }

    serde_json::json!({"value": current})
}

/// json_merge: a (dict), b (dict) → result (dict)
/// Shallow merge: b overwrites a.
fn json_merge(inputs: &Value) -> Value {
    let mut result = inputs["a"]
        .as_object()
        .cloned()
        .unwrap_or_default();

    if let Some(b) = inputs["b"].as_object() {
        for (k, v) in b {
            result.insert(k.clone(), v.clone());
        }
    }

    serde_json::json!({"result": Value::Object(result)})
}

// =============================================================================
// Math ops
// =============================================================================

fn extract_nums(inputs: &Value) -> Vec<f64> {
    inputs["values"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_f64())
                .collect()
        })
        .unwrap_or_default()
}

/// math_sum: values (list of numbers) → result
fn math_sum(inputs: &Value) -> Value {
    let nums = extract_nums(inputs);
    serde_json::json!({"result": nums.iter().sum::<f64>()})
}

/// math_mean: values (list of numbers) → result
fn math_mean(inputs: &Value) -> Value {
    let nums = extract_nums(inputs);
    let result = if nums.is_empty() {
        0.0
    } else {
        nums.iter().sum::<f64>() / nums.len() as f64
    };
    serde_json::json!({"result": result})
}

/// math_max: values (list of numbers) → result
fn math_max(inputs: &Value) -> Value {
    let nums = extract_nums(inputs);
    let result = nums.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    serde_json::json!({"result": result})
}

/// math_min: values (list of numbers) → result
fn math_min(inputs: &Value) -> Value {
    let nums = extract_nums(inputs);
    let result = nums.iter().copied().fold(f64::INFINITY, f64::min);
    serde_json::json!({"result": result})
}

// =============================================================================
// Export all ops via C ABI
// =============================================================================

export_ops!(
    double,
    add,
    hash_chain,
    string_concat,
    string_split,
    string_template,
    json_parse,
    json_extract,
    json_merge,
    math_sum,
    math_mean,
    math_max,
    math_min,
);
