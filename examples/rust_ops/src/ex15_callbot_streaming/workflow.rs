//! ex15_callbot_streaming — callbot pipeline ops (audio, VAD, STT, intent, TTS).

use hush_serve::hush_op;
use serde_json::{json, Value};

/// customer_audio: sample_count -> array of {audio, timestamp_ms}
#[hush_op(generator)]
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

/// vad: audio, timestamp_ms -> conditional yield (voice activity detection)
#[hush_op(generator)]
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

/// stt: segment, start_ms, end_ms -> transcript
#[hush_op]
pub fn stt(inputs: &Value) -> Value {
    let segment = inputs["segment"].as_str().unwrap_or("");
    let start_ms = &inputs["start_ms"];
    let end_ms = &inputs["end_ms"];

    json!({
        "transcript": format!("Hello from {} [{}-{}ms]", segment, start_ms, end_ms)
    })
}

/// classify_intent: transcript -> intent, confidence
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

/// handle_intent: intent, transcript -> response
#[hush_op]
pub fn handle_intent(inputs: &Value) -> Value {
    let intent = inputs["intent"].as_str().unwrap_or("");
    let transcript = inputs["transcript"].as_str().unwrap_or("");

    if intent == "greeting" {
        json!({
            "response": "Hello! How can I help you today?",
        })
    } else {
        json!({
            "response": format!("I understand. Let me help with: {}", transcript),
        })
    }
}

/// tts: response -> array of {audio_out, index}
#[hush_op(generator)]
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
    fn test_customer_audio() {
        let result = customer_audio(&json!({"sample_count": 3}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 3);
        assert_eq!(arr[0], json!({"audio": "chunk_0", "timestamp_ms": 0}));
        assert_eq!(arr[2], json!({"audio": "chunk_2", "timestamp_ms": 64}));
    }

    #[test]
    fn test_vad_speech_detected() {
        let result = vad(&json!({"audio": "chunk_2", "timestamp_ms": 64}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 1);
        assert_eq!(arr[0]["segment"], "speech_from_chunk_2");
    }

    #[test]
    fn test_vad_silence() {
        let result = vad(&json!({"audio": "chunk_0", "timestamp_ms": 0}));
        let arr = result.as_array().unwrap();
        assert_eq!(arr.len(), 0);
    }

    #[test]
    fn test_stt() {
        let result = stt(&json!({"segment": "speech_from_chunk_2", "start_ms": 64, "end_ms": 96}));
        assert_eq!(result["transcript"], "Hello from speech_from_chunk_2 [64-96ms]");
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
    }

    #[test]
    fn test_handle_intent_greeting() {
        let result = handle_intent(&json!({"intent": "greeting", "transcript": "xin chào"}));
        assert_eq!(result["response"], "Hello! How can I help you today?");
    }

    #[test]
    fn test_handle_intent_general() {
        let result = handle_intent(&json!({"intent": "question", "transcript": "thời tiết"}));
        assert!(result["response"].as_str().unwrap().contains("thời tiết"));
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
