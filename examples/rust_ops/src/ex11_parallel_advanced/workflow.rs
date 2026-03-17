//! ex11_parallel_advanced — fan-out/fan-in, generator iteration, partial failure ops.

use hush_serve::hush_op;
use serde_json::{json, Value};

/// analyze_sentiment: text -> sentiment
#[hush_op]
pub fn analyze_sentiment(input: &Value) -> Value {
    let text = input["text"].as_str().unwrap_or("");
    let positive_words = ["good", "great", "excellent", "love", "happy"];
    let negative_words = ["bad", "terrible", "hate", "awful", "sad"];

    let words: Vec<&str> = text.split_whitespace().collect();
    let pos_count = words
        .iter()
        .filter(|w| positive_words.contains(&w.to_lowercase().as_str()))
        .count();
    let neg_count = words
        .iter()
        .filter(|w| negative_words.contains(&w.to_lowercase().as_str()))
        .count();

    let sentiment = if pos_count > neg_count {
        "positive"
    } else if neg_count > pos_count {
        "negative"
    } else {
        "neutral"
    };

    json!({ "sentiment": sentiment })
}

/// extract_keywords: text -> keywords (top 5 non-stop words > 2 chars)
#[hush_op]
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

/// count_stats: text -> word_count, char_count, avg_word_len
#[hush_op]
pub fn count_stats(input: &Value) -> Value {
    let text = input["text"].as_str().unwrap_or("");
    let words: Vec<&str> = text.split_whitespace().collect();
    let word_count = words.len();
    let char_count = text.len();
    let avg_word_len = if word_count > 0 {
        (char_count as f64 / word_count as f64 * 10.0).round() / 10.0
    } else {
        0.0
    };

    json!({
        "word_count": word_count,
        "char_count": char_count,
        "avg_word_len": avg_word_len,
    })
}

/// merge_analysis: s, k, wc, cc, awl -> analysis
#[hush_op]
pub fn merge_analysis(inputs: &Value) -> Value {
    json!({
        "analysis": {
            "sentiment": inputs["s"].clone(),
            "keywords": inputs["k"].clone(),
            "word_count": inputs["wc"].clone(),
            "char_count": inputs["cc"].clone(),
            "avg_word_len": inputs["awl"].clone(),
        }
    })
}

/// each_item: items -> [{"item": x} for x in items]
#[hush_op(generator)]
pub fn each_item(inputs: &Value) -> Value {
    match inputs["items"].as_array() {
        Some(arr) => arr.iter().map(|x| json!({"item": x})).collect::<Vec<_>>().into(),
        None => json!([]),
    }
}

/// process_item: item -> result = item*item, status = "ok"
#[hush_op]
pub fn process_item(inputs: &Value) -> Value {
    let item = inputs["item"].as_i64().unwrap_or(0);
    json!({"result": item * item, "status": "ok"})
}

/// safe_process: item -> if odd: result=item*10, error=null; else: result=null, error="Even number: {item}"
#[hush_op]
pub fn safe_process(inputs: &Value) -> Value {
    let item = inputs["item"].as_i64().unwrap_or(0);
    if item % 2 != 0 {
        json!({"result": item * 10, "error": null})
    } else {
        json!({"result": null, "error": format!("Even number: {}", item)})
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_analyze_sentiment_positive() {
        let result = analyze_sentiment(&json!({"text": "I love this great product"}));
        assert_eq!(result["sentiment"], "positive");
    }

    #[test]
    fn test_analyze_sentiment_negative() {
        let result = analyze_sentiment(&json!({"text": "This is bad and terrible"}));
        assert_eq!(result["sentiment"], "negative");
    }

    #[test]
    fn test_analyze_sentiment_neutral() {
        let result = analyze_sentiment(&json!({"text": "This is a product"}));
        assert_eq!(result["sentiment"], "neutral");
    }

    #[test]
    fn test_extract_keywords() {
        let result = extract_keywords(&json!({"text": "This is a great excellent product"}));
        let kw = result["keywords"].as_array().unwrap();
        assert!(kw.len() <= 5);
        assert!(kw.contains(&json!("great")));
    }

    #[test]
    fn test_count_stats() {
        let result = count_stats(&json!({"text": "hello world foo"}));
        assert_eq!(result["word_count"], 3);
        assert_eq!(result["char_count"], 15);
        assert_eq!(result["avg_word_len"], 5.0);
    }

    #[test]
    fn test_merge_analysis() {
        let result = merge_analysis(&json!({
            "s": "positive",
            "k": ["rust", "hush"],
            "wc": 100,
            "cc": 500,
            "awl": 5.0,
        }));
        assert_eq!(result["analysis"]["sentiment"], "positive");
        assert_eq!(result["analysis"]["keywords"], json!(["rust", "hush"]));
    }

    #[test]
    fn test_each_item() {
        let result = each_item(&json!({"items": [1, 2, 3]}));
        assert_eq!(result, json!([{"item": 1}, {"item": 2}, {"item": 3}]));
    }

    #[test]
    fn test_process_item() {
        let result = process_item(&json!({"item": 7}));
        assert_eq!(result["result"], 49);
        assert_eq!(result["status"], "ok");
    }

    #[test]
    fn test_safe_process_odd() {
        let result = safe_process(&json!({"item": 3}));
        assert_eq!(result["result"], 30);
        assert!(result["error"].is_null());
    }

    #[test]
    fn test_safe_process_even() {
        let result = safe_process(&json!({"item": 4}));
        assert!(result["result"].is_null());
        assert_eq!(result["error"], "Even number: 4");
    }
}
