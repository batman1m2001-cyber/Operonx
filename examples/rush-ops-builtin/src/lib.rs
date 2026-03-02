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

/// identity: passes through all inputs as outputs.
/// Returns the full input object as-is.
fn identity(inputs: &Value) -> Value {
    match inputs {
        Value::Object(map) => Value::Object(map.clone()),
        other => serde_json::json!({"out": other}),
    }
}

/// multiply: a, b → result = a * b
fn multiply(inputs: &Value) -> Value {
    match (inputs["a"].as_i64(), inputs["b"].as_i64()) {
        (Some(a), Some(b)) => serde_json::json!({"result": a * b}),
        _ => {
            let a = inputs["a"].as_f64().unwrap_or(0.0);
            let b = inputs["b"].as_f64().unwrap_or(0.0);
            serde_json::json!({"result": a * b})
        }
    }
}

/// square: n → result = n * n
fn square(inputs: &Value) -> Value {
    if let Some(n) = inputs["n"].as_i64() {
        serde_json::json!({"result": n * n})
    } else if let Some(n) = inputs["n"].as_f64() {
        serde_json::json!({"result": n * n})
    } else {
        serde_json::json!({"error": "missing or invalid input 'n'"})
    }
}

/// increment: x → result = x + 1
fn increment(inputs: &Value) -> Value {
    if let Some(x) = inputs["x"].as_i64() {
        serde_json::json!({"result": x + 1})
    } else {
        serde_json::json!({"error": "missing or invalid input 'x'"})
    }
}

/// greet: name → greeting = "Hello, {name}!"
fn greet(inputs: &Value) -> Value {
    let name = inputs["name"].as_str().unwrap_or("World");
    serde_json::json!({"greeting": format!("Hello, {}!", name)})
}

/// make_dict: key, value → {key: value}
fn make_dict(inputs: &Value) -> Value {
    let key = inputs["key"].as_str().unwrap_or("key").to_string();
    let value = inputs["value"].clone();
    let mut map = serde_json::Map::new();
    map.insert(key, value);
    serde_json::json!({"out": Value::Object(map)})
}

/// to_upper: text → result = text.upper()
fn to_upper(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    serde_json::json!({"result": text.to_uppercase()})
}

/// join_strings: items (list), separator → result
fn join_strings(inputs: &Value) -> Value {
    let sep = inputs["separator"].as_str().unwrap_or("");
    let items = inputs["items"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .map(|v| match v {
                    Value::String(s) => s.clone(),
                    other => other.to_string(),
                })
                .collect::<Vec<_>>()
                .join(sep)
        })
        .unwrap_or_default();
    serde_json::json!({"result": items})
}

/// dbl: value → result = value * 2 (alias for double with different param name)
fn dbl(inputs: &Value) -> Value {
    if let Some(v) = inputs["value"].as_i64() {
        serde_json::json!({"result": v * 2})
    } else if let Some(v) = inputs["value"].as_f64() {
        serde_json::json!({"result": v * 2.0})
    } else {
        serde_json::json!({"error": "missing or invalid input 'value'"})
    }
}

/// ok_op: value → result = value * 10
fn ok_op(inputs: &Value) -> Value {
    if let Some(v) = inputs["value"].as_i64() {
        serde_json::json!({"result": v * 10})
    } else {
        serde_json::json!({"error": "missing or invalid input 'value'"})
    }
}

/// make_list: returns {"items": [10, 20, 30]}
fn make_list(_inputs: &Value) -> Value {
    serde_json::json!({"items": [10, 20, 30]})
}

/// increment_counter: counter → new_counter = counter + 1
fn increment_counter(inputs: &Value) -> Value {
    if let Some(c) = inputs["counter"].as_i64() {
        serde_json::json!({"new_counter": c + 1})
    } else {
        serde_json::json!({"error": "missing or invalid input 'counter'"})
    }
}

/// accumulate: total, step_size → new_total = total + step_size
fn accumulate(inputs: &Value) -> Value {
    let total = inputs["total"].as_i64().unwrap_or(0);
    let step = inputs["step_size"].as_i64().unwrap_or(0);
    serde_json::json!({"new_total": total + step})
}

/// fib_step: a, b → new_a = b, new_b = a + b
fn fib_step(inputs: &Value) -> Value {
    let a = inputs["a"].as_i64().unwrap_or(0);
    let b = inputs["b"].as_i64().unwrap_or(0);
    serde_json::json!({"new_a": b, "new_b": a + b})
}

/// tagged_op: x → result = x * 2, $tags = ["fast", "cached"]
fn tagged_op(inputs: &Value) -> Value {
    let x = inputs["x"].as_i64().unwrap_or(0);
    serde_json::json!({"result": x * 2, "$tags": ["fast", "cached"]})
}

/// safe_op: x → result = x + 1
fn safe_op(inputs: &Value) -> Value {
    if let Some(x) = inputs["x"].as_i64() {
        serde_json::json!({"result": x + 1})
    } else {
        serde_json::json!({"error": "missing input 'x'"})
    }
}

/// multiply_values: value, multiplier → result = value * multiplier
fn multiply_values(inputs: &Value) -> Value {
    let v = inputs["value"].as_i64().unwrap_or(0);
    let m = inputs["multiplier"].as_i64().unwrap_or(1);
    serde_json::json!({"result": v * m})
}

/// noop: does nothing, returns empty object. Used for testing.
fn noop(_inputs: &Value) -> Value {
    serde_json::json!({})
}

/// passthrough: returns all inputs wrapped in "out" key.
/// For single-value passthrough: value → out = value
fn passthrough(inputs: &Value) -> Value {
    if let Some(v) = inputs.get("value") {
        serde_json::json!({"out": v})
    } else {
        // Return all inputs as outputs
        inputs.clone()
    }
}

/// fail_op: always raises an error. For testing error handling.
fn fail_op(inputs: &Value) -> Value {
    let msg = inputs["message"].as_str().unwrap_or("Intentional test failure");
    serde_json::json!({"error": msg, "$error": true})
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
    identity,
    multiply,
    square,
    increment,
    greet,
    make_dict,
    to_upper,
    join_strings,
    dbl,
    ok_op,
    make_list,
    increment_counter,
    accumulate,
    fib_step,
    tagged_op,
    safe_op,
    multiply_values,
    noop,
    passthrough,
    fail_op,
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
