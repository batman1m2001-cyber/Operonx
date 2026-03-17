use serde_json::{json, Value};
use hush_serve::hush_op;

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

#[hush_op]
pub fn aggregate_stats(input: &Value) -> Value {
    let scores = input["scores"]
        .as_array()
        .cloned()
        .unwrap_or_default();

    let nums: Vec<f64> = scores
        .iter()
        .filter_map(|v| v.as_f64())
        .collect();

    let total: f64 = nums.iter().sum();
    let average = if nums.is_empty() {
        0.0
    } else {
        total / nums.len() as f64
    };

    let mut tags = vec!["aggregated".to_string()];
    if average > 20.0 {
        tags.push("high-score".to_string());
    }

    json!({
        "total": total,
        "average": average,
        "$tags": tags,
    })
}

#[hush_op]
pub fn classify_by_count(input: &Value) -> Value {
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

#[hush_op]
pub fn classify_by_score(input: &Value) -> Value {
    let score = input["score"].as_f64().unwrap_or(0.0);

    let category = if score > 50.0 {
        "high"
    } else if score > 25.0 {
        "medium"
    } else {
        "low"
    };

    json!({
        "category": category,
        "$tags": [format!("category:{}", category)],
    })
}

#[hush_op]
pub fn analyze_text(input: &Value) -> Value {
    let text = input["text"].as_str().unwrap_or("");
    let words: Vec<&str> = text.split_whitespace().collect();
    let word_count = words.len();
    let preview: String = text.chars().take(50).collect();

    let mut tags = vec!["analyzed".to_string()];
    if word_count > 10 {
        tags.push("long-text".to_string());
    }

    json!({
        "word_count": word_count,
        "preview": preview,
        "$tags": tags,
    })
}

#[hush_op]
pub fn tokenize(input: &Value) -> Value {
    let text = input["text"].as_str().unwrap_or("");
    let tokens: Vec<&str> = text.split_whitespace().collect();
    let count = tokens.len();

    let mut tags = vec!["tokenized".to_string()];
    if count > 5 {
        tags.push("many-tokens".to_string());
    }

    json!({
        "tokens": tokens,
        "count": count,
        "$tags": tags,
    })
}

#[hush_op]
pub fn analyze_chunk(input: &Value) -> Value {
    let chunk = input["chunk"].as_str().unwrap_or("");
    let index = input["index"].as_u64().unwrap_or(0);

    let words: Vec<&str> = chunk.split_whitespace().collect();
    let word_count = words.len();
    let has_long_word = words.iter().any(|w| w.len() > 6);
    let score = word_count * 10 + if has_long_word { 15 } else { 0 };

    let star = if has_long_word { "*" } else { "" };
    let result = format!("[{}] {}w score={}{}", index, word_count, score, star);

    json!({ "result": result })
}

#[hush_op]
pub fn classify_intent(input: &Value) -> Value {
    let transcript = input["transcript"].as_str().unwrap_or("");
    let lower = transcript.to_lowercase();

    let (intent, confidence) = if lower.contains("hello") {
        ("greeting", 0.95)
    } else {
        ("general", 0.8)
    };

    json!({
        "intent": intent,
        "confidence": confidence,
    })
}

/// aggregate_data: data (list of numbers) → total, average, count
#[hush_op]
pub fn aggregate_data(input: &Value) -> Value {
    let data = input["data"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let nums: Vec<f64> = data.iter().filter_map(|v| v.as_f64()).collect();
    let count = nums.len();
    let total: f64 = nums.iter().sum();
    let average = if count > 0 {
        total / count as f64
    } else {
        0.0
    };
    json!({
        "total": total,
        "average": average,
        "count": count,
    })
}

#[hush_op]
pub fn summarize_products(input: &Value) -> Value {
    let products = input["products"]
        .as_array()
        .cloned()
        .unwrap_or_default();

    let total: f64 = products.iter().filter_map(|v| v.as_f64()).sum();

    json!({ "total": total })
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
    fn test_count_stats() {
        let result = count_stats(&json!({"text": "hello world foo"}));
        assert_eq!(result["word_count"], 3);
        assert_eq!(result["char_count"], 15);
        assert_eq!(result["avg_word_len"], 5.0);
    }

    #[test]
    fn test_count_stats_empty() {
        let result = count_stats(&json!({"text": ""}));
        assert_eq!(result["word_count"], 0);
        assert_eq!(result["avg_word_len"], 0.0);
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

    #[test]
    fn test_aggregate_stats_low() {
        let result = aggregate_stats(&json!({"scores": [5, 10, 15]}));
        assert_eq!(result["total"], 30.0);
        assert_eq!(result["average"], 10.0);
        assert_eq!(result["$tags"], json!(["aggregated"]));
    }

    #[test]
    fn test_aggregate_stats_high() {
        let result = aggregate_stats(&json!({"scores": [30, 40, 50]}));
        assert_eq!(result["total"], 120.0);
        assert_eq!(result["average"], 40.0);
        assert_eq!(result["$tags"], json!(["aggregated", "high-score"]));
    }

    #[test]
    fn test_classify_by_count_article() {
        let result = classify_by_count(&json!({"word_count": 25}));
        assert_eq!(result["category"], "article");
        assert_eq!(result["$tags"], json!(["category:article"]));
    }

    #[test]
    fn test_classify_by_count_sentence() {
        let result = classify_by_count(&json!({"word_count": 7}));
        assert_eq!(result["category"], "sentence");
    }

    #[test]
    fn test_classify_by_count_phrase() {
        let result = classify_by_count(&json!({"word_count": 3}));
        assert_eq!(result["category"], "phrase");
    }

    #[test]
    fn test_classify_by_score_high() {
        let result = classify_by_score(&json!({"score": 75}));
        assert_eq!(result["category"], "high");
        assert_eq!(result["$tags"], json!(["category:high"]));
    }

    #[test]
    fn test_classify_by_score_medium() {
        let result = classify_by_score(&json!({"score": 30}));
        assert_eq!(result["category"], "medium");
    }

    #[test]
    fn test_classify_by_score_low() {
        let result = classify_by_score(&json!({"score": 10}));
        assert_eq!(result["category"], "low");
    }

    #[test]
    fn test_analyze_text_short() {
        let result = analyze_text(&json!({"text": "hello world"}));
        assert_eq!(result["word_count"], 2);
        assert_eq!(result["preview"], "hello world");
        assert_eq!(result["$tags"], json!(["analyzed"]));
    }

    #[test]
    fn test_analyze_text_long() {
        let text = "one two three four five six seven eight nine ten eleven";
        let result = analyze_text(&json!({"text": text}));
        assert_eq!(result["word_count"], 11);
        assert_eq!(result["$tags"], json!(["analyzed", "long-text"]));
    }

    #[test]
    fn test_tokenize_few() {
        let result = tokenize(&json!({"text": "a b c"}));
        assert_eq!(result["count"], 3);
        assert_eq!(result["tokens"], json!(["a", "b", "c"]));
        assert_eq!(result["$tags"], json!(["tokenized"]));
    }

    #[test]
    fn test_tokenize_many() {
        let result = tokenize(&json!({"text": "a b c d e f g"}));
        assert_eq!(result["count"], 7);
        assert_eq!(result["$tags"], json!(["tokenized", "many-tokens"]));
    }

    #[test]
    fn test_analyze_chunk() {
        let result = analyze_chunk(&json!({"chunk": "hello beautiful world", "index": 2}));
        // 3 words, "beautiful" > 6 chars => score = 30 + 15 = 45
        assert_eq!(result["result"], "[2] 3w score=45*");
    }

    #[test]
    fn test_analyze_chunk_no_long_word() {
        let result = analyze_chunk(&json!({"chunk": "hi there", "index": 0}));
        // 2 words, no long word => score = 20
        assert_eq!(result["result"], "[0] 2w score=20");
    }

    #[test]
    fn test_classify_intent_greeting() {
        let result = classify_intent(&json!({"transcript": "Hello, how are you?"}));
        assert_eq!(result["intent"], "greeting");
        assert_eq!(result["confidence"], 0.95);
    }

    #[test]
    fn test_classify_intent_general() {
        let result = classify_intent(&json!({"transcript": "I need help with my order"}));
        assert_eq!(result["intent"], "general");
        assert_eq!(result["confidence"], 0.8);
    }

    #[test]
    fn test_summarize_products() {
        let result = summarize_products(&json!({"products": [10, 20, 30]}));
        assert_eq!(result["total"], 60.0);
    }

    #[test]
    fn test_summarize_products_empty() {
        let result = summarize_products(&json!({"products": []}));
        assert_eq!(result["total"], 0.0);
    }
}
