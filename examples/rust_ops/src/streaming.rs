//! Streaming ops — Rust equivalents of Python async generator ops.
//!
//! In Rust mode these return arrays instead of async-yielding,
//! so the engine can push each element downstream as a StreamCell.

use serde_json::{json, Value};

/// chunk_text: text, chunk_size → array of {chunk, index}
///
/// Splits text by whitespace into words, groups into chunks of `chunk_size` words.
/// Each chunk is the words joined by space, index is 0-based.
///
/// Python equivalent:
/// ```python
/// @op(rust="chunk_text")
/// async def chunk_text(text: str, chunk_size: int):
///     words = text.split()
///     for i, start in enumerate(range(0, len(words), chunk_size)):
///         yield {"chunk": " ".join(words[start:start+chunk_size]), "index": i}
/// ```
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

/// async_counter: n → array of {number, squared} for i in 1..=n
///
/// Python equivalent:
/// ```python
/// @op(rust="async_counter")
/// async def async_counter(n: int):
///     for i in range(1, n + 1):
///         yield {"number": i, "squared": i * i}
/// ```
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

/// format_square: number, squared → {label: "{number}^2 = {squared}"}
///
/// Single-item transform (not a generator), formats a squared result as a label.
pub fn format_square(inputs: &Value) -> Value {
    let number = &inputs["number"];
    let squared = &inputs["squared"];
    json!({
        "label": format!("{}^2 = {}", number, squared)
    })
}

/// customer_audio: sample_count → array of {audio, timestamp_ms}
///
/// Simulates an audio source producing `sample_count` chunks at 32ms intervals.
///
/// Python equivalent:
/// ```python
/// @op(rust="customer_audio")
/// async def customer_audio(sample_count: int):
///     for i in range(sample_count):
///         yield {"audio": f"chunk_{i}", "timestamp_ms": i * 32}
/// ```
pub fn customer_audio(inputs: &Value) -> Value {
    let sample_count = inputs["sample_count"].as_i64().unwrap_or(0);

    let items: Vec<Value> = (0..sample_count)
        .map(|i| {
            json!({
                "audio": format!("chunk_{}", i),
                "timestamp_ms": i * 32
            })
        })
        .collect();

    Value::Array(items)
}

/// vad: audio, timestamp_ms → conditional yield (voice activity detection)
///
/// Returns speech segments only at timestamp_ms 64 or 128 (simulated speech).
/// Otherwise returns empty array (silence, no speech detected).
pub fn vad(inputs: &Value) -> Value {
    let audio = inputs["audio"].as_str().unwrap_or("");
    let timestamp_ms = inputs["timestamp_ms"].as_i64().unwrap_or(0);

    if timestamp_ms == 64 || timestamp_ms == 128 {
        json!([{
            "segment": format!("speech_from_{}", audio),
            "start_ms": timestamp_ms,
            "end_ms": timestamp_ms + 32
        }])
    } else {
        json!([])
    }
}

/// stt: segment, start_ms, end_ms → {transcript}
///
/// Simulated speech-to-text transcription.
pub fn stt(inputs: &Value) -> Value {
    let segment = inputs["segment"].as_str().unwrap_or("");
    let start_ms = &inputs["start_ms"];
    let end_ms = &inputs["end_ms"];

    json!({
        "transcript": format!("Hello from {} [{}-{}ms]", segment, start_ms, end_ms)
    })
}

/// tts: response → array of {audio_out, index}
///
/// Simulated text-to-speech: splits response by whitespace, produces one
/// audio chunk per word.
///
/// Python equivalent:
/// ```python
/// @op(rust="tts")
/// async def tts(response: str):
///     for i, word in enumerate(response.split()):
///         yield {"audio_out": f"tts_{i}_{word}", "index": i}
/// ```
pub fn tts(inputs: &Value) -> Value {
    let response = inputs["response"].as_str().unwrap_or("");

    let items: Vec<Value> = response
        .split_whitespace()
        .enumerate()
        .map(|(i, word)| {
            json!({
                "audio_out": format!("tts_{}_{}", i, word),
                "index": i
            })
        })
        .collect();

    Value::Array(items)
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
        assert_eq!(arr[1]["index"], 1);
        assert_eq!(arr[2]["chunk"], "five");
        assert_eq!(arr[2]["index"], 2);
    }

    #[test]
    fn test_chunk_text_empty() {
        let result = chunk_text(&json!({"text": "", "chunk_size": 3}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 0);
    }

    #[test]
    fn test_async_counter() {
        let result = async_counter(&json!({"n": 3}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 3);
        assert_eq!(arr[0], json!({"number": 1, "squared": 1}));
        assert_eq!(arr[1], json!({"number": 2, "squared": 4}));
        assert_eq!(arr[2], json!({"number": 3, "squared": 9}));
    }

    #[test]
    fn test_async_counter_zero() {
        let result = async_counter(&json!({"n": 0}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 0);
    }

    #[test]
    fn test_format_square() {
        let result = format_square(&json!({"number": 5, "squared": 25}));
        assert_eq!(result["label"], "5^2 = 25");
    }

    #[test]
    fn test_customer_audio() {
        let result = customer_audio(&json!({"sample_count": 3}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 3);
        assert_eq!(arr[0], json!({"audio": "chunk_0", "timestamp_ms": 0}));
        assert_eq!(arr[1], json!({"audio": "chunk_1", "timestamp_ms": 32}));
        assert_eq!(arr[2], json!({"audio": "chunk_2", "timestamp_ms": 64}));
    }

    #[test]
    fn test_vad_speech_detected() {
        let result = vad(&json!({"audio": "chunk_2", "timestamp_ms": 64}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 1);
        assert_eq!(arr[0]["segment"], "speech_from_chunk_2");
        assert_eq!(arr[0]["start_ms"], 64);
        assert_eq!(arr[0]["end_ms"], 96);
    }

    #[test]
    fn test_vad_speech_at_128() {
        let result = vad(&json!({"audio": "chunk_4", "timestamp_ms": 128}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 1);
        assert_eq!(arr[0]["segment"], "speech_from_chunk_4");
        assert_eq!(arr[0]["start_ms"], 128);
        assert_eq!(arr[0]["end_ms"], 160);
    }

    #[test]
    fn test_vad_silence() {
        let result = vad(&json!({"audio": "chunk_0", "timestamp_ms": 0}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 0);

        let result = vad(&json!({"audio": "chunk_3", "timestamp_ms": 96}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 0);
    }

    #[test]
    fn test_stt() {
        let result = stt(&json!({"segment": "speech_from_chunk_2", "start_ms": 64, "end_ms": 96}));
        assert_eq!(result["transcript"], "Hello from speech_from_chunk_2 [64-96ms]");
    }

    #[test]
    fn test_tts() {
        let result = tts(&json!({"response": "hello world"}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 2);
        assert_eq!(arr[0], json!({"audio_out": "tts_0_hello", "index": 0}));
        assert_eq!(arr[1], json!({"audio_out": "tts_1_world", "index": 1}));
    }

    #[test]
    fn test_tts_empty() {
        let result = tts(&json!({"response": ""}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 0);
    }
}
