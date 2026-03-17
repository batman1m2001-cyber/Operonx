//! ex02_data_pipeline — data transformation and text processing ops.

use hush_serve::hush_op;
use serde_json::{json, Value};

/// fetch_data: () -> {"data": [1, 2, 3, 4, 5]}
#[hush_op]
pub fn fetch_data(_inputs: &Value) -> Value {
    json!({"data": [1, 2, 3, 4, 5]})
}

/// transform: data (array) -> {"transformed": [x*2 for x in data]}
#[hush_op]
pub fn transform(inputs: &Value) -> Value {
    let transformed: Vec<Value> = inputs["data"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .map(|v| {
                    if let Some(n) = v.as_i64() {
                        json!(n * 2)
                    } else if let Some(n) = v.as_f64() {
                        json!(n * 2.0)
                    } else {
                        v.clone()
                    }
                })
                .collect()
        })
        .unwrap_or_default();
    json!({"transformed": transformed})
}

/// aggregate: data (list of numbers) -> total, average, count
#[hush_op]
pub fn aggregate(inputs: &Value) -> Value {
    let empty = vec![];
    let data = inputs["data"].as_array().unwrap_or(&empty);
    let nums: Vec<f64> = data.iter().filter_map(|v| v.as_f64()).collect();
    let total: f64 = nums.iter().sum();
    let count = nums.len();
    let average = if count > 0 { total / count as f64 } else { 0.0 };
    json!({"total": total as i64, "average": average, "count": count})
}

/// clean_text: text -> cleaned_text (strip, normalize whitespace, lowercase)
#[hush_op]
pub fn clean_text(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    let cleaned: String = text
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    json!({"cleaned_text": cleaned})
}

/// count_words: text -> word_count, unique_words, words
#[hush_op]
pub fn count_words(input: &Value) -> Value {
    let text = input["text"].as_str().unwrap_or("");
    let words: Vec<&str> = text.split_whitespace().collect();
    let word_count = words.len();
    let unique_words = {
        let mut seen = std::collections::HashSet::new();
        for w in &words {
            seen.insert(*w);
        }
        seen.len()
    };
    let word_list: Vec<&str> = words;

    json!({
        "word_count": word_count,
        "unique_words": unique_words,
        "words": word_list,
    })
}

/// summarize_stats: word_count, unique_words, cleaned_text -> report
#[hush_op]
pub fn summarize_stats(input: &Value) -> Value {
    let word_count = input["word_count"].as_u64().unwrap_or(0);
    let unique_words = input["unique_words"].as_u64().unwrap_or(0);
    let _cleaned_text = input["cleaned_text"].as_str().unwrap_or("");

    let pct = if word_count > 0 {
        (unique_words as f64 / word_count as f64 * 100.0).round() as u64
    } else {
        0
    };

    let report = format!(
        "Văn bản có {} từ, {} từ unique, tỉ lệ unique: {}%",
        word_count, unique_words, pct
    );

    json!({ "report": report })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fetch_data() {
        let result = fetch_data(&json!({}));
        assert_eq!(result["data"], json!([1, 2, 3, 4, 5]));
    }

    #[test]
    fn test_transform() {
        let result = transform(&json!({"data": [1, 2, 3, 4, 5]}));
        assert_eq!(result["transformed"], json!([2, 4, 6, 8, 10]));
    }

    #[test]
    fn test_aggregate() {
        let result = aggregate(&json!({"data": [2, 4, 6, 8, 10]}));
        assert_eq!(result["total"], 30);
        assert_eq!(result["count"], 5);
    }

    #[test]
    fn test_clean_text() {
        let result = clean_text(&json!({"text": "  Hello   World  "}));
        assert_eq!(result["cleaned_text"], "hello world");
    }

    #[test]
    fn test_count_words() {
        let result = count_words(&json!({"text": "hello world hello"}));
        assert_eq!(result["word_count"], 3);
        assert_eq!(result["unique_words"], 2);
        assert_eq!(result["words"], json!(["hello", "world", "hello"]));
    }

    #[test]
    fn test_summarize_stats() {
        let result = summarize_stats(&json!({
            "word_count": 10,
            "unique_words": 8,
            "cleaned_text": "some text"
        }));
        assert_eq!(
            result["report"],
            "Văn bản có 10 từ, 8 từ unique, tỉ lệ unique: 80%"
        );
    }
}
