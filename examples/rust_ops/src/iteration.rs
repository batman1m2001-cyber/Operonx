//! Iteration ops — Rust equivalents of Python generator (yield-based) ops.
//!
//! In Rust mode, generator ops return a JSON array of items instead of yielding.
//! The scheduler handles iterating over the array.
//!
//! Each function takes a `&serde_json::Value` (the op's inputs as JSON)
//! and returns a `serde_json::Value` (a JSON array of objects).

use serde_json::{json, Value};

macro_rules! iter_array {
    ($inputs:expr, $key:expr, $map:expr) => {{
        match $inputs[$key].as_array() {
            Some(arr) => arr.iter().map($map).collect::<Vec<_>>().into(),
            None => json!([]),
        }
    }};
}

/// each_item: items → [{"item": x} for x in items]
pub fn each_item(inputs: &Value) -> Value {
    iter_array!(inputs, "items", |x| json!({"item": x}))
}

/// each_item_with_prefix: items, prefix → [{"item": x, "prefix": prefix} for x in items]
pub fn each_item_with_prefix(inputs: &Value) -> Value {
    let prefix = inputs["prefix"].clone();
    iter_array!(inputs, "items", |x| json!({"item": x, "prefix": prefix}))
}

/// each_number: numbers → [{"x": n} for n in numbers]
pub fn each_number(inputs: &Value) -> Value {
    iter_array!(inputs, "numbers", |n| json!({"x": n}))
}

/// each_token: tokens → [{"token": t} for t in tokens]
pub fn each_token(inputs: &Value) -> Value {
    iter_array!(inputs, "tokens", |t| json!({"token": t}))
}

/// each_x: xs → [{"x": x} for x in xs]
pub fn each_x(inputs: &Value) -> Value {
    iter_array!(inputs, "xs", |x| json!({"x": x}))
}

/// each_y: ys → [{"y": y} for y in ys]
pub fn each_y(inputs: &Value) -> Value {
    iter_array!(inputs, "ys", |y| json!({"y": y}))
}

/// each_fruit: → [{"item": "apple"}, {"item": "banana"}, {"item": "cherry"}]
pub fn each_fruit(_inputs: &Value) -> Value {
    json!([
        {"item": "apple"},
        {"item": "banana"},
        {"item": "cherry"}
    ])
}

/// each_value: items → [{"value": item} for item in items]
pub fn each_value(inputs: &Value) -> Value {
    iter_array!(inputs, "items", |item| json!({"value": item}))
}

/// halve_until: value → [{"value": v}, ...] while v >= 5, halving each time
pub fn halve_until(inputs: &Value) -> Value {
    let mut v = inputs["value"].as_i64().unwrap_or(0);
    let mut results = Vec::new();
    while v >= 5 {
        results.push(json!({"value": v}));
        v /= 2;
    }
    results.into()
}

/// halve_until_threshold: value, threshold → same but configurable threshold + tags
pub fn halve_until_threshold(inputs: &Value) -> Value {
    let mut v = inputs["value"].as_i64().unwrap_or(0);
    let threshold = inputs["threshold"].as_i64().unwrap_or(5);
    let mut results = Vec::new();
    while v >= threshold {
        results.push(json!({"value": v, "$tags": ["halved"]}));
        v /= 2;
    }
    if v > 0 {
        results.push(json!({"value": v, "$tags": ["halved", "below-threshold"]}));
    }
    results.into()
}

/// outer_iter: xs → [{"x": x} for x in xs]
pub fn outer_iter(inputs: &Value) -> Value {
    iter_array!(inputs, "xs", |x| json!({"x": x}))
}

/// inner_iter: ys, x → [{"product": x * y} for y in ys]
pub fn inner_iter(inputs: &Value) -> Value {
    let x = inputs["x"].as_i64().unwrap_or(0);
    iter_array!(inputs, "ys", |y| {
        let y_val = y.as_i64().unwrap_or(0);
        json!({"product": x * y_val})
    })
}

/// each_query: queries → [{"query": q} for q in queries]
pub fn each_query(inputs: &Value) -> Value {
    iter_array!(inputs, "queries", |q| json!({"query": q}))
}

/// each_outer: values → [{"outer": v} for v in values]
pub fn each_outer(inputs: &Value) -> Value {
    iter_array!(inputs, "values", |v| json!({"outer": v}))
}

/// each_inner: values → [{"inner": v} for v in values]
pub fn each_inner(inputs: &Value) -> Value {
    iter_array!(inputs, "values", |v| json!({"inner": v}))
}

/// emit_items: items → [{"x": item} for item in items]
pub fn emit_items(inputs: &Value) -> Value {
    iter_array!(inputs, "items", |item| json!({"x": item}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_each_item() {
        let result = each_item(&json!({"items": [1, 2, 3]}));
        assert_eq!(result, json!([{"item": 1}, {"item": 2}, {"item": 3}]));
    }

    #[test]
    fn test_each_item_empty() {
        let result = each_item(&json!({"items": []}));
        assert_eq!(result, json!([]));
    }

    #[test]
    fn test_each_item_with_prefix() {
        let result = each_item_with_prefix(&json!({"items": ["x", "y"], "prefix": "hello"}));
        assert_eq!(
            result,
            json!([{"item": "x", "prefix": "hello"}, {"item": "y", "prefix": "hello"}])
        );
    }

    #[test]
    fn test_each_number() {
        let result = each_number(&json!({"numbers": [10, 20, 30]}));
        assert_eq!(result, json!([{"x": 10}, {"x": 20}, {"x": 30}]));
    }

    #[test]
    fn test_halve_until() {
        let result = halve_until(&json!({"value": 40}));
        assert_eq!(
            result,
            json!([{"value": 40}, {"value": 20}, {"value": 10}, {"value": 5}])
        );
    }

    #[test]
    fn test_inner_iter() {
        let result = inner_iter(&json!({"ys": [10, 20], "x": 3}));
        assert_eq!(result, json!([{"product": 30}, {"product": 60}]));
    }

    #[test]
    fn test_each_fruit() {
        let result = each_fruit(&json!({}));
        assert_eq!(
            result,
            json!([{"item": "apple"}, {"item": "banana"}, {"item": "cherry"}])
        );
    }

    #[test]
    fn test_missing_input_returns_empty() {
        assert_eq!(each_item(&json!({})), json!([]));
        assert_eq!(each_number(&json!({})), json!([]));
        assert_eq!(each_token(&json!({})), json!([]));
        assert_eq!(each_x(&json!({})), json!([]));
        assert_eq!(each_y(&json!({})), json!([]));
    }
}
