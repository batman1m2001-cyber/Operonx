//! ex05_loops_and_branches — generator ops, branch ops.

use hush_serve::hush_op;
use serde_json::{json, Value};

/// each_item: items, prefix -> [{"item": x, "prefix": prefix} for x in items]
#[hush_op(generator)]
pub fn each_item(inputs: &Value) -> Value {
    let prefix = inputs.get("prefix").cloned();
    match inputs["items"].as_array() {
        Some(arr) => match prefix {
            Some(p) => arr.iter().map(|x| json!({"item": x, "prefix": p})).collect::<Vec<_>>().into(),
            None => arr.iter().map(|x| json!({"item": x})).collect::<Vec<_>>().into(),
        },
        None => json!([]),
    }
}

/// process_item: item, prefix -> result = "{prefix}: {item}"
#[hush_op]
pub fn process_item(inputs: &Value) -> Value {
    let item = inputs["item"].as_str().unwrap_or("");
    let prefix = inputs["prefix"].as_str().unwrap_or("");
    json!({"result": format!("{}: {}", prefix, item)})
}

/// each_number: numbers -> [{"x": n} for n in numbers]
#[hush_op(generator)]
pub fn each_number(inputs: &Value) -> Value {
    match inputs["numbers"].as_array() {
        Some(arr) => arr.iter().map(|n| json!({"x": n})).collect::<Vec<_>>().into(),
        None => json!([]),
    }
}

/// square: x -> squared = x * x
#[hush_op]
pub fn square(inputs: &Value) -> Value {
    if let Some(x) = inputs["x"].as_i64() {
        json!({"squared": x * x})
    } else if let Some(x) = inputs["x"].as_f64() {
        json!({"squared": x * x})
    } else {
        json!({"error": "missing or invalid input 'x'"})
    }
}

/// halve_until: value -> [{"value": v}, ...] while v >= 5, halving each time
#[hush_op(generator)]
pub fn halve_until(inputs: &Value) -> Value {
    let mut v = inputs["value"].as_i64().unwrap_or(0);
    let mut results = Vec::new();
    while v >= 5 {
        v /= 2;
        results.push(json!({"value": v}));
    }
    results.into()
}

/// excellent: () -> {"grade": "A", "message": "Xuat sac!"}
#[hush_op]
pub fn excellent(_inputs: &Value) -> Value {
    json!({"grade": "A", "message": "Xuất sắc!"})
}

/// good: () -> {"grade": "B", "message": "Tot!"}
#[hush_op]
pub fn good(_inputs: &Value) -> Value {
    json!({"grade": "B", "message": "Tốt!"})
}

/// average: () -> {"grade": "C", "message": "Trung binh"}
#[hush_op]
pub fn average(_inputs: &Value) -> Value {
    json!({"grade": "C", "message": "Trung bình"})
}

/// fail: () -> {"grade": "F", "message": "Can cai thien"}
#[hush_op]
pub fn fail(_inputs: &Value) -> Value {
    json!({"grade": "F", "message": "Cần cải thiện"})
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
    fn test_each_item_with_prefix() {
        let result = each_item(&json!({"items": ["x", "y"], "prefix": "hello"}));
        assert_eq!(
            result,
            json!([{"item": "x", "prefix": "hello"}, {"item": "y", "prefix": "hello"}])
        );
    }

    #[test]
    fn test_process_item() {
        let result = process_item(&json!({"item": "hello", "prefix": ">>"}));
        assert_eq!(result["result"], ">>: hello");
    }

    #[test]
    fn test_each_number() {
        let result = each_number(&json!({"numbers": [10, 20, 30]}));
        assert_eq!(result, json!([{"x": 10}, {"x": 20}, {"x": 30}]));
    }

    #[test]
    fn test_square() {
        let result = square(&json!({"x": 5}));
        assert_eq!(result["squared"], 25);
    }

    #[test]
    fn test_halve_until() {
        let result = halve_until(&json!({"value": 40}));
        assert_eq!(
            result,
            json!([{"value": 20}, {"value": 10}, {"value": 5}, {"value": 2}])
        );
    }

    #[test]
    fn test_excellent() {
        let result = excellent(&json!({}));
        assert_eq!(result["grade"], "A");
        assert_eq!(result["message"], "Xuất sắc!");
    }

    #[test]
    fn test_good() {
        let result = good(&json!({}));
        assert_eq!(result["grade"], "B");
    }

    #[test]
    fn test_average() {
        let result = average(&json!({}));
        assert_eq!(result["grade"], "C");
    }

    #[test]
    fn test_fail() {
        let result = fail(&json!({}));
        assert_eq!(result["grade"], "F");
    }
}
