//! ex14_streaming_tracing — generator ops for streaming pipelines.

use hush_serve::hush_op;
use serde_json::{json, Value};

/// chunk_text: text, chunk_size -> array of {chunk, index}
#[hush_op(generator)]
pub fn chunk_text(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    let chunk_size = inputs["chunk_size"].as_u64().unwrap_or(1) as usize;
    let chunk_size = chunk_size.max(1);

    let words: Vec<&str> = text.split_whitespace().collect();
    let chunks: Vec<Value> = words
        .chunks(chunk_size)
        .enumerate()
        .map(|(i, group)| {
            json!({
                "chunk": group.join(" "),
                "index": i
            })
        })
        .collect();

    Value::Array(chunks)
}

/// analyze_chunk: chunk, index -> result
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

/// async_counter: n -> array of {number, squared} for i in 1..=n
#[hush_op(generator)]
pub fn async_counter(inputs: &Value) -> Value {
    let n = inputs["n"].as_i64().unwrap_or(0);

    let items: Vec<Value> = (1..=n)
        .map(|i| {
            json!({
                "number": i,
                "squared": i * i
            })
        })
        .collect();

    Value::Array(items)
}

/// format_square: number, squared -> label = "{number}^2 = {squared}"
#[hush_op]
pub fn format_square(inputs: &Value) -> Value {
    let number = inputs["number"].as_i64().unwrap_or(0);
    let squared = inputs["squared"].as_i64().unwrap_or(0);
    json!({"label": format!("{}^2 = {}", number, squared)})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_chunk_text_basic() {
        let result = chunk_text(&json!({"text": "one two three four five", "chunk_size": 2}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 3);
        assert_eq!(arr[0]["chunk"], "one two");
        assert_eq!(arr[0]["index"], 0);
        assert_eq!(arr[1]["chunk"], "three four");
        assert_eq!(arr[2]["chunk"], "five");
    }

    #[test]
    fn test_chunk_text_empty() {
        let result = chunk_text(&json!({"text": "", "chunk_size": 3}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 0);
    }

    #[test]
    fn test_analyze_chunk() {
        let result = analyze_chunk(&json!({"chunk": "hello beautiful world", "index": 2}));
        assert_eq!(result["result"], "[2] 3w score=45*");
    }

    #[test]
    fn test_analyze_chunk_no_long_word() {
        let result = analyze_chunk(&json!({"chunk": "hi there", "index": 0}));
        assert_eq!(result["result"], "[0] 2w score=20");
    }

    #[test]
    fn test_async_counter() {
        let result = async_counter(&json!({"n": 3}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 3);
        assert_eq!(arr[0], json!({"number": 1, "squared": 1}));
        assert_eq!(arr[2], json!({"number": 3, "squared": 9}));
    }

    #[test]
    fn test_format_square() {
        let result = format_square(&json!({"number": 3, "squared": 9}));
        assert_eq!(result["label"], "3^2 = 9");
    }
}
