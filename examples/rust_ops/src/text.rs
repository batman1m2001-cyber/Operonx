//! Text ops — string processing for workflow pipelines.

use serde_json::{json, Value};

/// greet: name → greeting = "Hello, {name}!"
///
/// Python equivalent:
/// ```python
/// @op(rust="greet")
/// def greet(name: str):
///     return {"greeting": f"Hello, {name}!"}
/// ```
pub fn greet(inputs: &Value) -> Value {
    let name = inputs["name"].as_str().unwrap_or("World");
    json!({"greeting": format!("Hello, {}!", name)})
}

/// to_upper: text → result = text.upper()
pub fn to_upper(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    json!({"result": text.to_uppercase()})
}

/// string_template: template, vars → result
///
/// Simple `{key}` substitution.
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

    json!({"result": output})
}

/// string_concat: parts (list of str) → result (str)
pub fn string_concat(inputs: &Value) -> Value {
    let result = inputs["parts"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .map(|v| v.as_str().unwrap_or(""))
                .collect::<Vec<_>>()
                .join("")
        })
        .unwrap_or_default();
    json!({"result": result})
}

/// greet_vi: name → greeting = "Xin chào, {name}!"
pub fn greet_vi(inputs: &Value) -> Value {
    let name = inputs["name"].as_str().unwrap_or("World");
    json!({"greeting": format!("Xin chào, {}!", name)})
}

/// clean_text: text → cleaned_text (strip, normalize whitespace, lowercase)
pub fn clean_text(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    let cleaned: String = text
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    json!({"cleaned_text": cleaned})
}

/// preprocess: text → cleaned, $tags (strip + lowercase + tagging)
pub fn preprocess(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    let cleaned = text.trim().to_lowercase();
    let mut tags = vec![json!("preprocessed")];
    if cleaned.len() > 30 {
        tags.push(json!("long-text"));
    }
    json!({"cleaned": cleaned, "$tags": tags})
}

/// add_prefix: text, prefix → result = "{prefix}: {text}"
pub fn add_prefix(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    let prefix = inputs["prefix"].as_str().unwrap_or("");
    json!({"result": format!("{}: {}", prefix, text)})
}

/// process_item_text: item, prefix → result = "{prefix}: {item}"
pub fn process_item_text(inputs: &Value) -> Value {
    let item = inputs["item"].as_str().unwrap_or("");
    let prefix = inputs["prefix"].as_str().unwrap_or("");
    json!({"result": format!("{}: {}", prefix, item)})
}

/// handle_success: result → output = "Result: {result}"
pub fn handle_success(inputs: &Value) -> Value {
    let result = inputs["result"].as_f64().unwrap_or(0.0);
    json!({"output": format!("Result: {}", result)})
}

/// handle_error: error → output = "Error occurred: {error}"
pub fn handle_error(inputs: &Value) -> Value {
    let error = inputs["error"].as_str().unwrap_or("unknown");
    json!({"output": format!("Error occurred: {}", error)})
}

/// extract_keywords: text → keywords (top 5 non-stop words > 2 chars)
pub fn extract_keywords(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    let stop_words = [
        "the", "is", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
    ];
    let keywords: Vec<String> = text
        .split_whitespace()
        .map(|w| w.to_lowercase().trim_matches(|c: char| ".,!?".contains(c)).to_string())
        .filter(|w| w.len() > 2 && !stop_words.contains(&w.as_str()))
        .take(5)
        .collect();
    json!({"keywords": keywords})
}

/// grade_to_message: grade → message
pub fn grade_to_message(inputs: &Value) -> Value {
    let grade = inputs["grade"].as_str().unwrap_or("");
    let message = match grade {
        "A" => "Xuất sắc!",
        "B" => "Tốt!",
        "C" => "Trung bình",
        "D" => "Yếu",
        "F" => "Cần cải thiện",
        _ => "Unknown",
    };
    json!({"message": message})
}

/// format_labeled: value, label → formatted = "{label}: {value}"
pub fn format_labeled(inputs: &Value) -> Value {
    let label = inputs["label"].as_str().unwrap_or("");
    let value = &inputs["value"];
    let v_str = match value {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    };
    json!({"formatted": format!("{}: {}", label, v_str)})
}

/// format_square: number, squared → label = "{number}^2 = {squared}"
pub fn format_square(inputs: &Value) -> Value {
    let number = inputs["number"].as_i64().unwrap_or(0);
    let squared = inputs["squared"].as_i64().unwrap_or(0);
    json!({"label": format!("{}^2 = {}", number, squared)})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_greet() {
        let result = greet(&json!({"name": "Hush"}));
        assert_eq!(result["greeting"], "Hello, Hush!");
    }

    #[test]
    fn test_greet_vi() {
        let result = greet_vi(&json!({"name": "Hush"}));
        assert_eq!(result["greeting"], "Xin chào, Hush!");
    }

    #[test]
    fn test_to_upper() {
        let result = to_upper(&json!({"text": "hello world"}));
        assert_eq!(result["result"], "HELLO WORLD");
    }

    #[test]
    fn test_clean_text() {
        let result = clean_text(&json!({"text": "  Hello   World  "}));
        assert_eq!(result["cleaned_text"], "hello world");
    }

    #[test]
    fn test_extract_keywords() {
        let result = extract_keywords(&json!({"text": "This is a great excellent product"}));
        let kw = result["keywords"].as_array().unwrap();
        assert!(kw.len() <= 5);
        assert!(kw.contains(&json!("great")));
    }

    #[test]
    fn test_add_prefix() {
        let result = add_prefix(&json!({"text": "hello", "prefix": ">>"}));
        assert_eq!(result["result"], ">>: hello");
    }

    #[test]
    fn test_grade_to_message() {
        let result = grade_to_message(&json!({"grade": "A"}));
        assert_eq!(result["message"], "Xuất sắc!");
    }

    #[test]
    fn test_format_square() {
        let result = format_square(&json!({"number": 3, "squared": 9}));
        assert_eq!(result["label"], "3^2 = 9");
    }

    #[test]
    fn test_string_template() {
        let result = string_template(&json!({
            "template": "Hello {name}, you have {count} messages",
            "vars": {"name": "Hush", "count": "5"}
        }));
        assert_eq!(result["result"], "Hello Hush, you have 5 messages");
    }
}
