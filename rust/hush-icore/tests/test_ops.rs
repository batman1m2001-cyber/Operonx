//! Built-in Rust op implementations for the Hush workflow engine.
//!
//! Contains ops across categories: core, string, JSON, math, benchmark, and test support.
//!
//! Usage in Python:
//! ```python
//! @op(rust="double")
//! def double(x: int):
//!     return {"result": x * 2}  # Python fallback
//! ```

use serde_json::{self, Value};

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};

// =============================================================================
// Core ops
// =============================================================================

/// double: x → result = x * 2
/// Accepts both int and float inputs.
pub fn double(inputs: &Value) -> Value {
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
pub fn add(inputs: &Value) -> Value {
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
pub fn hash_chain(inputs: &Value) -> Value {
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
pub fn identity(inputs: &Value) -> Value {
    match inputs {
        Value::Object(map) => Value::Object(map.clone()),
        other => serde_json::json!({"out": other}),
    }
}

/// multiply: a, b → result = a * b
pub fn multiply(inputs: &Value) -> Value {
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
pub fn square(inputs: &Value) -> Value {
    if let Some(n) = inputs["n"].as_i64() {
        serde_json::json!({"result": n * n})
    } else if let Some(n) = inputs["n"].as_f64() {
        serde_json::json!({"result": n * n})
    } else {
        serde_json::json!({"error": "missing or invalid input 'n'"})
    }
}

/// increment: x → result = x + 1
pub fn increment(inputs: &Value) -> Value {
    if let Some(x) = inputs["x"].as_i64() {
        serde_json::json!({"result": x + 1})
    } else {
        serde_json::json!({"error": "missing or invalid input 'x'"})
    }
}

/// greet: name → greeting = "Hello, {name}!"
pub fn greet(inputs: &Value) -> Value {
    let name = inputs["name"].as_str().unwrap_or("World");
    serde_json::json!({"greeting": format!("Hello, {}!", name)})
}

/// greet_vi: name → greeting = "Xin chào, {name}!"
pub fn greet_vi(inputs: &Value) -> Value {
    let name = inputs["name"].as_str().unwrap_or("World");
    serde_json::json!({"greeting": format!("Xin chào, {}!", name)})
}

/// make_dict: key, value → {key: value}
pub fn make_dict(inputs: &Value) -> Value {
    let key = inputs["key"].as_str().unwrap_or("key").to_string();
    let value = inputs["value"].clone();
    let mut map = serde_json::Map::new();
    map.insert(key, value);
    serde_json::json!({"out": Value::Object(map)})
}

/// to_upper: text → result = text.upper()
pub fn to_upper(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    serde_json::json!({"result": text.to_uppercase()})
}

/// join_strings: items (list), separator → result
pub fn join_strings(inputs: &Value) -> Value {
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
pub fn dbl(inputs: &Value) -> Value {
    if let Some(v) = inputs["value"].as_i64() {
        serde_json::json!({"result": v * 2})
    } else if let Some(v) = inputs["value"].as_f64() {
        serde_json::json!({"result": v * 2.0})
    } else {
        serde_json::json!({"error": "missing or invalid input 'value'"})
    }
}

/// ok_op: value → result = value * 10
pub fn ok_op(inputs: &Value) -> Value {
    if let Some(v) = inputs["value"].as_i64() {
        serde_json::json!({"result": v * 10})
    } else {
        serde_json::json!({"error": "missing or invalid input 'value'"})
    }
}

/// make_list: returns {"items": [10, 20, 30]}
pub fn make_list(_inputs: &Value) -> Value {
    serde_json::json!({"items": [10, 20, 30]})
}

/// increment_counter: counter → new_counter = counter + 1
pub fn increment_counter(inputs: &Value) -> Value {
    if let Some(c) = inputs["counter"].as_i64() {
        serde_json::json!({"new_counter": c + 1})
    } else {
        serde_json::json!({"error": "missing or invalid input 'counter'"})
    }
}

/// accumulate: total, step_size → new_total = total + step_size
pub fn accumulate(inputs: &Value) -> Value {
    let total = inputs["total"].as_i64().unwrap_or(0);
    let step = inputs["step_size"].as_i64().unwrap_or(0);
    serde_json::json!({"new_total": total + step})
}

/// fib_step: a, b → new_a = b, new_b = a + b
pub fn fib_step(inputs: &Value) -> Value {
    let a = inputs["a"].as_i64().unwrap_or(0);
    let b = inputs["b"].as_i64().unwrap_or(0);
    serde_json::json!({"new_a": b, "new_b": a + b})
}

/// tagged_op: x → result = x * 2, $tags = ["fast", "cached"]
pub fn tagged_op(inputs: &Value) -> Value {
    let x = inputs["x"].as_i64().unwrap_or(0);
    serde_json::json!({"result": x * 2, "$tags": ["fast", "cached"]})
}

/// safe_op: x → result = x + 1
pub fn safe_op(inputs: &Value) -> Value {
    if let Some(x) = inputs["x"].as_i64() {
        serde_json::json!({"result": x + 1})
    } else {
        serde_json::json!({"error": "missing input 'x'"})
    }
}

/// multiply_values: value, multiplier → result = value * multiplier
pub fn multiply_values(inputs: &Value) -> Value {
    let v = inputs["value"].as_i64().unwrap_or(0);
    let m = inputs["multiplier"].as_i64().unwrap_or(1);
    serde_json::json!({"result": v * m})
}

/// noop: does nothing, returns empty object. Used for testing.
pub fn noop(_inputs: &Value) -> Value {
    serde_json::json!({})
}

/// passthrough: returns all inputs wrapped in "out" key.
/// For single-value passthrough: value → out = value
pub fn passthrough(inputs: &Value) -> Value {
    if let Some(v) = inputs.get("value") {
        serde_json::json!({"out": v})
    } else {
        // Return all inputs as outputs
        inputs.clone()
    }
}

/// fail_op: always raises an error. For testing error handling.
pub fn fail_op(inputs: &Value) -> Value {
    let msg = inputs["message"].as_str().unwrap_or("Intentional test failure");
    serde_json::json!({"error": msg, "$error": true})
}

// =============================================================================
// String ops
// =============================================================================

/// string_concat: parts (list of str) → result (str)
pub fn string_concat(inputs: &Value) -> Value {
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
pub fn string_split(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    let delimiter = inputs["delimiter"].as_str().unwrap_or("");
    let parts: Vec<&str> = text.split(delimiter).collect();
    serde_json::json!({"parts": parts})
}

/// string_template: template, vars → result (str)
/// Simple {key} substitution.
pub fn string_template(inputs: &Value) -> Value {
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
pub fn json_parse(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("null");
    match serde_json::from_str::<Value>(text) {
        Ok(data) => serde_json::json!({"data": data}),
        Err(e) => serde_json::json!({"error": format!("JSON parse error: {}", e)}),
    }
}

/// json_extract: data, path (dot-separated) → value
pub fn json_extract(inputs: &Value) -> Value {
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
pub fn json_merge(inputs: &Value) -> Value {
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
pub fn math_sum(inputs: &Value) -> Value {
    let nums = extract_nums(inputs);
    serde_json::json!({"result": nums.iter().sum::<f64>()})
}

/// math_mean: values (list of numbers) → result
pub fn math_mean(inputs: &Value) -> Value {
    let nums = extract_nums(inputs);
    let result = if nums.is_empty() {
        0.0
    } else {
        nums.iter().sum::<f64>() / nums.len() as f64
    };
    serde_json::json!({"result": result})
}

/// math_max: values (list of numbers) → result
pub fn math_max(inputs: &Value) -> Value {
    let nums = extract_nums(inputs);
    let result = nums.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    serde_json::json!({"result": result})
}

/// math_min: values (list of numbers) → result
pub fn math_min(inputs: &Value) -> Value {
    let nums = extract_nums(inputs);
    let result = nums.iter().copied().fold(f64::INFINITY, f64::min);
    serde_json::json!({"result": result})
}

// =============================================================================
// Benchmark ops
// =============================================================================

/// bench_noop: x → {"x": x} (passthrough for benchmarks)
pub fn bench_noop(inputs: &Value) -> Value {
    let x = &inputs["x"];
    serde_json::json!({"x": x})
}

/// classify: score → {"grade": str, "score": int}
pub fn classify(inputs: &Value) -> Value {
    let score = inputs["score"].as_i64().unwrap_or(0);
    let grade = if score >= 90 {
        "excellent"
    } else if score >= 70 {
        "good"
    } else if score >= 50 {
        "average"
    } else {
        "fail"
    };
    serde_json::json!({"grade": grade, "score": score})
}

/// process_grade: grade, score → {"result": "grade:score"}
pub fn process_grade(inputs: &Value) -> Value {
    let grade = inputs["grade"].as_str().unwrap_or("");
    let score = inputs["score"].as_i64().unwrap_or(0);
    serde_json::json!({"result": format!("{}:{}", grade, score)})
}

/// aggregate: results (list) → {"summary": len}
pub fn aggregate(inputs: &Value) -> Value {
    let len = inputs["results"]
        .as_array()
        .map(|a| a.len())
        .unwrap_or(0);
    serde_json::json!({"summary": len})
}

/// bench_transform: item, prefix → {"output": "prefix-item"}
pub fn bench_transform(inputs: &Value) -> Value {
    let item = inputs["item"].as_str().unwrap_or("");
    let prefix = inputs["prefix"].as_str().unwrap_or("");
    serde_json::json!({"output": format!("{}-{}", prefix, item)})
}

/// merge_two: a, b → {"merged": a, "x": a}
pub fn merge_two(inputs: &Value) -> Value {
    let a = &inputs["a"];
    serde_json::json!({"merged": a, "x": a})
}

/// combine_all: r1..r5 → {"combined": [...], "count": n}
pub fn combine_all(inputs: &Value) -> Value {
    let mut parts = Vec::new();
    for key in &["r1", "r2", "r3", "r4", "r5"] {
        if !inputs[*key].is_null() {
            parts.push(inputs[*key].clone());
        }
    }
    let count = parts.len();
    serde_json::json!({"combined": parts, "count": count})
}

/// cpu_hash_chain: x, iterations → {"hash": hex16, "x": x}
pub fn cpu_hash_chain(inputs: &Value) -> Value {
    let x = inputs["x"].as_i64().unwrap_or(0);
    let iterations = inputs["iterations"].as_i64().unwrap_or(0);
    let mut hasher = DefaultHasher::new();
    let mut current = format!("{}", x);
    for _ in 0..iterations {
        current.hash(&mut hasher);
        current = format!("{:016x}", hasher.finish());
        hasher = DefaultHasher::new();
    }
    serde_json::json!({"hash": &current[..16.min(current.len())], "x": x})
}

/// cpu_prime_sieve: limit → {"prime_count": count}
pub fn cpu_prime_sieve(inputs: &Value) -> Value {
    let limit = inputs["limit"].as_u64().unwrap_or(100) as usize;
    let mut sieve = vec![true; limit + 1];
    if limit >= 1 {
        sieve[0] = false;
        if limit >= 2 {
            sieve[1] = false;
        }
    }
    let mut i = 2;
    while i * i <= limit {
        if sieve[i] {
            let mut j = i * i;
            while j <= limit {
                sieve[j] = false;
                j += i;
            }
        }
        i += 1;
    }
    let count: usize = sieve.iter().filter(|&&b| b).count();
    serde_json::json!({"prime_count": count})
}

/// cpu_matrix_mult: size → {"trace": float}
pub fn cpu_matrix_mult(inputs: &Value) -> Value {
    let size = inputs["size"].as_u64().unwrap_or(10) as usize;
    let mut a = vec![vec![0.0f64; size]; size];
    let mut b = vec![vec![0.0f64; size]; size];
    for i in 0..size {
        for j in 0..size {
            a[i][j] = (i + j) as f64;
            b[i][j] = (i * j + 1) as f64;
        }
    }
    let mut trace = 0.0f64;
    for i in 0..size {
        let mut s = 0.0f64;
        for k in 0..size {
            s += a[i][k] * b[k][i];
        }
        trace += s;
    }
    serde_json::json!({"trace": trace})
}

/// cpu_fibonacci: n → {"fib": result}
pub fn cpu_fibonacci(inputs: &Value) -> Value {
    let n = inputs["n"].as_u64().unwrap_or(0);
    let modulus: u64 = 1_000_000_007;
    let (mut a, mut b): (u64, u64) = (0, 1);
    for _ in 0..n {
        let tmp = (a + b) % modulus;
        a = b;
        b = tmp;
    }
    serde_json::json!({"fib": a})
}

// =============================================================================
// Test support ops
// =============================================================================

/// grade_a: returns A grade with message (for branch tests)
pub fn grade_a(_inputs: &Value) -> Value {
    serde_json::json!({"grade": "A", "message": "Excellent!"})
}

/// grade_b: returns B grade with message (for branch tests)
pub fn grade_b(_inputs: &Value) -> Value {
    serde_json::json!({"grade": "B", "message": "Good job!"})
}

/// grade_f: returns F grade with message (for branch tests)
pub fn grade_f(_inputs: &Value) -> Value {
    serde_json::json!({"grade": "F", "message": "Try again!"})
}

/// grade_a_mult: returns multiplier=3 (for branch+iteration tests)
pub fn grade_a_mult(_inputs: &Value) -> Value {
    serde_json::json!({"multiplier": 3})
}

/// grade_f_mult: returns multiplier=1 (for branch+iteration tests)
pub fn grade_f_mult(_inputs: &Value) -> Value {
    serde_json::json!({"multiplier": 1})
}

/// prefix: text → out = "[INFO] {text}"
pub fn prefix(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    serde_json::json!({"out": format!("[INFO] {}", text)})
}

/// sum_all: a,b,c,d,e → total = a+b+c+d+e
pub fn sum_all(inputs: &Value) -> Value {
    let total: i64 = ["a", "b", "c", "d", "e"]
        .iter()
        .filter_map(|k| inputs[*k].as_i64())
        .sum();
    serde_json::json!({"total": total})
}

/// classify_value: value → label ("high" if >=50, else "low"), value
pub fn classify_value(inputs: &Value) -> Value {
    let value = inputs["value"].as_i64().unwrap_or(0);
    let label = if value >= 50 { "high" } else { "low" };
    serde_json::json!({"label": label, "value": value})
}

/// multi_output: x → doubled, tripled, squared
pub fn multi_output(inputs: &Value) -> Value {
    let x = inputs["x"].as_i64().unwrap_or(0);
    serde_json::json!({"doubled": x * 2, "tripled": x * 3, "squared": x * x})
}

/// multi: x → a=x+1, b=x+2, c=x+3
pub fn multi(inputs: &Value) -> Value {
    let x = inputs["x"].as_i64().unwrap_or(0);
    serde_json::json!({"a": x + 1, "b": x + 2, "c": x + 3})
}

/// compute: x → result = x * 10
pub fn compute(inputs: &Value) -> Value {
    let x = inputs["x"].as_i64().unwrap_or(0);
    serde_json::json!({"result": x * 10})
}

/// process_list: items → count, sum
pub fn process_list(inputs: &Value) -> Value {
    let items = inputs["items"].as_array();
    let count = items.map(|a| a.len()).unwrap_or(0);
    let sum: i64 = items
        .map(|a| a.iter().filter_map(|v| v.as_i64()).sum())
        .unwrap_or(0);
    serde_json::json!({"count": count, "sum": sum})
}

/// measure: text → length, first (10 chars), last (10 chars)
pub fn measure(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    let len = text.len();
    let first: String = text.chars().take(10).collect();
    let last: String = if len >= 10 {
        text.chars().skip(len - 10).collect()
    } else {
        text.to_string()
    };
    serde_json::json!({"length": len, "first": first, "last": last})
}

/// append_char: text, char, counter → new_text = text + char, new_counter = counter + 1
pub fn append_char(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    let ch = inputs["char"].as_str().unwrap_or("");
    let counter = inputs["counter"].as_i64().unwrap_or(0);
    serde_json::json!({"new_text": format!("{}{}", text, ch), "new_counter": counter + 1})
}

/// collect_op: items, counter → new_items = items + [counter²], new_counter = counter + 1
pub fn collect_op(inputs: &Value) -> Value {
    let mut items = inputs["items"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let counter = inputs["counter"].as_i64().unwrap_or(0);
    items.push(serde_json::json!(counter * counter));
    serde_json::json!({"new_items": items, "new_counter": counter + 1})
}

/// make_start: returns {start: 90}
pub fn make_start(_inputs: &Value) -> Value {
    serde_json::json!({"start": 90})
}

/// format_grade: grade → formatted = "Grade: {grade}"
pub fn format_grade(inputs: &Value) -> Value {
    let grade = inputs["grade"].as_str().unwrap_or("");
    serde_json::json!({"formatted": format!("Grade: {}", grade)})
}

/// multiply_vf: value, factor → result = value * factor
pub fn multiply_vf(inputs: &Value) -> Value {
    let value = inputs["value"].as_i64().unwrap_or(0);
    let factor = inputs["factor"].as_i64().unwrap_or(1);
    serde_json::json!({"result": value * factor})
}

/// accumulate_step: total, step → new_total = total + step
pub fn accumulate_step(inputs: &Value) -> Value {
    let total = inputs["total"].as_i64().unwrap_or(0);
    let step = inputs["step"].as_i64().unwrap_or(0);
    serde_json::json!({"new_total": total + step})
}

/// raise_op: always returns an error (for testing error resilience)
pub fn raise_op(inputs: &Value) -> Value {
    let x = inputs["x"].as_i64().unwrap_or(0);
    serde_json::json!({"error": format!("boom with x={}", x), "$error": true})
}

/// maybe_fail: returns value*10, but errors on value==2
pub fn maybe_fail(inputs: &Value) -> Value {
    let value = inputs["value"].as_i64().unwrap_or(0);
    if value == 2 {
        serde_json::json!({"error": "fail on 2", "$error": true})
    } else {
        serde_json::json!({"result": value * 10})
    }
}

// =============================================================================
// Pipeline ops (for examples 01 & 02)
// =============================================================================

/// step_a: () → {"a_result": "Kết quả A"}
pub fn step_a(_inputs: &Value) -> Value {
    serde_json::json!({"a_result": "Kết quả A"})
}

/// step_b: () → {"b_result": "Kết quả B"}
pub fn step_b(_inputs: &Value) -> Value {
    serde_json::json!({"b_result": "Kết quả B"})
}

/// fetch_data: () → {"data": [1, 2, 3, 4, 5]}
pub fn fetch_data(_inputs: &Value) -> Value {
    serde_json::json!({"data": [1, 2, 3, 4, 5]})
}

/// transform_double: data (array) → {"transformed": [x*2 for x in data]}
pub fn transform_double(inputs: &Value) -> Value {
    let transformed: Vec<Value> = inputs["data"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .map(|v| {
                    if let Some(n) = v.as_i64() {
                        serde_json::json!(n * 2)
                    } else if let Some(n) = v.as_f64() {
                        serde_json::json!(n * 2.0)
                    } else {
                        v.clone()
                    }
                })
                .collect()
        })
        .unwrap_or_default();
    serde_json::json!({"transformed": transformed})
}

/// aggregate_data: data (array) → {total, average, count}
pub fn aggregate_data(inputs: &Value) -> Value {
    let nums: Vec<f64> = inputs["data"]
        .as_array()
        .map(|arr| arr.iter().filter_map(|v| v.as_f64()).collect())
        .unwrap_or_default();
    let count = nums.len();
    let total: f64 = nums.iter().sum();
    let average = if count > 0 { total / count as f64 } else { 0.0 };
    serde_json::json!({"total": total, "average": average, "count": count})
}

/// clean_text: text → cleaned_text (strip, normalize whitespace, lowercase)
pub fn clean_text(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    let cleaned: String = text
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    serde_json::json!({"cleaned_text": cleaned})
}

/// count_words: text → {word_count, unique_words, words}
pub fn count_words(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    let words: Vec<&str> = text.split_whitespace().collect();
    let word_count = words.len();
    let unique_words = {
        let mut seen = std::collections::HashSet::new();
        for w in &words {
            seen.insert(*w);
        }
        seen.len()
    };
    serde_json::json!({
        "word_count": word_count,
        "unique_words": unique_words,
        "words": words,
    })
}

/// summarize_stats: word_count, unique_words, cleaned_text → {report}
pub fn summarize_stats(inputs: &Value) -> Value {
    let word_count = inputs["word_count"].as_u64().unwrap_or(0);
    let unique_words = inputs["unique_words"].as_u64().unwrap_or(0);
    let pct = if word_count > 0 {
        (unique_words as f64 / word_count as f64 * 100.0).round() as u64
    } else {
        0
    };
    let report = format!(
        "Văn bản có {} từ, {} từ unique, tỉ lệ unique: {}%",
        word_count, unique_words, pct
    );
    serde_json::json!({"report": report})
}

// =============================================================================
// Generator ops (return Vec<Value> — each item is one yield)
// =============================================================================

/// chunk_text: text, chunk_size → yields [{"chunk": str}, ...]
/// Splits text into chunks of `chunk_size` characters.
pub fn chunk_text(inputs: &Value) -> Vec<Value> {
    let text = inputs["text"].as_str().unwrap_or("");
    let chunk_size = inputs["chunk_size"].as_u64().unwrap_or(100) as usize;

    if text.is_empty() || chunk_size == 0 {
        return Vec::new();
    }

    let chars: Vec<char> = text.chars().collect();
    chars
        .chunks(chunk_size)
        .enumerate()
        .map(|(i, chunk)| {
            let s: String = chunk.iter().collect();
            serde_json::json!({"chunk": s, "index": i})
        })
        .collect()
}

/// range_gen: start, end, step → yields [{"value": n}, ...]
/// Generates a range of integers.
pub fn range_gen(inputs: &Value) -> Vec<Value> {
    let start = inputs["start"].as_i64().unwrap_or(0);
    let end = inputs["end"].as_i64().unwrap_or(0);
    let step = inputs["step"].as_i64().unwrap_or(1);

    if step == 0 || (step > 0 && start >= end) || (step < 0 && start <= end) {
        return Vec::new();
    }

    let mut result = Vec::new();
    let mut current = start;
    while (step > 0 && current < end) || (step < 0 && current > end) {
        result.push(serde_json::json!({"value": current}));
        current += step;
    }
    result
}

// =============================================================================
// Example 05 — Loops & Branches ops
// =============================================================================

/// each_item_with_prefix: items, prefix → yields {item, prefix} per item
pub fn each_item_with_prefix(inputs: &Value) -> Vec<Value> {
    let items = inputs["items"].as_array().cloned().unwrap_or_default();
    let prefix = inputs["prefix"].as_str().unwrap_or("");
    items
        .into_iter()
        .map(|item| serde_json::json!({"item": item, "prefix": prefix}))
        .collect()
}

/// process_item_text: item, prefix → result = "{prefix}: {item}"
pub fn process_item_text(inputs: &Value) -> Value {
    let item = inputs["item"].as_str().unwrap_or("");
    let prefix = inputs["prefix"].as_str().unwrap_or("");
    serde_json::json!({"result": format!("{}: {}", prefix, item)})
}

/// each_number: numbers → yields {x} per number
pub fn each_number(inputs: &Value) -> Vec<Value> {
    let numbers = inputs["numbers"].as_array().cloned().unwrap_or_default();
    numbers
        .into_iter()
        .map(|x| serde_json::json!({"x": x}))
        .collect()
}

/// square_named: x → squared = x * x
pub fn square_named(inputs: &Value) -> Value {
    let x = inputs["x"].as_i64().unwrap_or(0);
    serde_json::json!({"squared": x * x})
}

/// halve_until: value → yields {value} while value >= 5, halving each time
pub fn halve_until(inputs: &Value) -> Vec<Value> {
    let mut value = inputs["value"].as_i64().unwrap_or(0);
    let mut result = Vec::new();
    while value >= 5 {
        value /= 2;
        result.push(serde_json::json!({"value": value}));
    }
    result
}

/// excellent: → grade="A", message="Xuất sắc!"
pub fn excellent(inputs: &Value) -> Value {
    let _ = inputs;
    serde_json::json!({"grade": "A", "message": "Xuất sắc!"})
}

/// good: → grade="B", message="Tốt!"
pub fn good(inputs: &Value) -> Value {
    let _ = inputs;
    serde_json::json!({"grade": "B", "message": "Tốt!"})
}

/// average_grade: → grade="C", message="Trung bình"
pub fn average_grade(inputs: &Value) -> Value {
    let _ = inputs;
    serde_json::json!({"grade": "C", "message": "Trung bình"})
}

/// fail_grade: → grade="F", message="Cần cải thiện"
pub fn fail_grade(inputs: &Value) -> Value {
    let _ = inputs;
    serde_json::json!({"grade": "F", "message": "Cần cải thiện"})
}

// =============================================================================
// Example 06 ops (tracing)
// =============================================================================

/// analyze_text: text → word_count, preview
pub fn analyze_text(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    let word_count = text.split_whitespace().count();
    let preview = if text.len() > 50 { &text[..50] } else { text };
    serde_json::json!({
        "word_count": word_count,
        "preview": preview,
    })
}

/// classify_by_count: word_count → category
pub fn classify_by_count(inputs: &Value) -> Value {
    let word_count = inputs["word_count"].as_i64().unwrap_or(0);
    let category = if word_count > 20 {
        "article"
    } else if word_count > 5 {
        "sentence"
    } else {
        "phrase"
    };
    serde_json::json!({
        "category": category,
    })
}
