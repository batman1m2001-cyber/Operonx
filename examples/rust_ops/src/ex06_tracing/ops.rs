//! ex06_tracing — text analysis and classification ops with tracing tags.

use hush_serve::hush_op;
use serde_json::{json, Value};

/// analyze_text: text -> word_count, preview, $tags
#[hush_op]
pub fn analyze_text(input: &Value) -> Value {
    let text = input["text"].as_str().unwrap_or("");
    let words: Vec<&str> = text.split_whitespace().collect();
    let word_count = words.len();
    let preview: String = text.chars().take(50).collect();

    let mut tags = vec!["analyzed".to_string()];
    if word_count > 10 {
        tags.push("long-text".to_string());
    } else {
        tags.push("short-text".to_string());
    }

    json!({
        "word_count": word_count,
        "preview": preview,
        "$tags": tags,
    })
}

/// classify: word_count -> category, $tags
#[hush_op]
pub fn classify(input: &Value) -> Value {
    let word_count = input["word_count"].as_u64().unwrap_or(0);

    let category = if word_count > 20 {
        "article"
    } else if word_count > 5 {
        "sentence"
    } else {
        "phrase"
    };

    json!({
        "category": category,
        "$tags": [format!("category:{}", category)],
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_analyze_text_short() {
        let result = analyze_text(&json!({"text": "hello world"}));
        assert_eq!(result["word_count"], 2);
        assert_eq!(result["preview"], "hello world");
        assert_eq!(result["$tags"], json!(["analyzed", "short-text"]));
    }

    #[test]
    fn test_analyze_text_long() {
        let text = "one two three four five six seven eight nine ten eleven";
        let result = analyze_text(&json!({"text": text}));
        assert_eq!(result["word_count"], 11);
        assert_eq!(result["$tags"], json!(["analyzed", "long-text"]));
    }

    #[test]
    fn test_classify_article() {
        let result = classify(&json!({"word_count": 25}));
        assert_eq!(result["category"], "article");
        assert_eq!(result["$tags"], json!(["category:article"]));
    }

    #[test]
    fn test_classify_sentence() {
        let result = classify(&json!({"word_count": 7}));
        assert_eq!(result["category"], "sentence");
    }

    #[test]
    fn test_classify_phrase() {
        let result = classify(&json!({"word_count": 3}));
        assert_eq!(result["category"], "phrase");
    }
}
