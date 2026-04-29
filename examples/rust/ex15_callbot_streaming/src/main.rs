//! 15 Callbot Streaming — Rust-side demo.
//!
//! Mirrors `examples/python/ex15_callbot_streaming/main.py`.
//!
//! ⚠️  Rust-runtime limitation: the callbot is built almost entirely on
//! generator ops + nested `@graph` composition — neither of which the
//! Rust scheduler exposes yet. The ops are declared here so the Python
//! side can serialise its `graph.json` and the Rust binary can be
//! exercised structurally; per-item dispatch and the nested router will
//! short-circuit until those land.

use operonx::{op, Operon};
use serde_json::Value;

#[op(name = "customer_audio")]
fn customer_audio(sample_count: i64) -> Value {
    let out: Vec<Value> = (0..sample_count)
        .map(|i| serde_json::json!({ "audio": format!("chunk_{i}"), "timestamp_ms": i * 32 }))
        .collect();
    serde_json::json!({ "items": out })
}

#[op(name = "vad")]
fn vad(audio: String, timestamp_ms: i64) -> Value {
    let speech_timestamps = [64_i64, 128_i64];
    if speech_timestamps.contains(&timestamp_ms) {
        serde_json::json!({
            "segment": format!("speech_from_{audio}"),
            "start_ms": timestamp_ms,
            "end_ms": timestamp_ms + 32,
        })
    } else {
        serde_json::json!({
            "segment": Value::Null,
            "start_ms": Value::Null,
            "end_ms": Value::Null,
        })
    }
}

#[op(name = "stt")]
fn stt(segment: String, start_ms: i64, end_ms: i64) -> Value {
    serde_json::json!({
        "transcript": format!("Hello from {segment} [{start_ms}-{end_ms}ms]"),
    })
}

#[op(name = "classify_intent")]
fn classify_intent(transcript: String) -> Value {
    if transcript.to_lowercase().contains("hello") {
        serde_json::json!({ "intent": "greeting", "confidence": 0.95 })
    } else {
        serde_json::json!({ "intent": "general", "confidence": 0.8 })
    }
}

#[op(name = "handle_intent")]
fn handle_intent(intent: String, transcript: String) -> Value {
    if intent == "greeting" {
        serde_json::json!({ "response": "Hello! How can I help you today?" })
    } else {
        serde_json::json!({ "response": format!("I understand. Let me help with: {transcript}") })
    }
}

#[op(name = "tts")]
fn tts(response: String) -> Value {
    let out: Vec<Value> = response
        .split_whitespace()
        .enumerate()
        .map(|(i, word)| serde_json::json!({ "audio_out": format!("tts_{i}_{word}"), "index": i }))
        .collect();
    serde_json::json!({ "items": out })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let here = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let graph_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("graph.json"))?)?;
    let inputs_bundle: Value =
        serde_json::from_slice(&std::fs::read(here.join("inputs.json"))?)?;

    let graph_v = graph_bundle
        .get("callbot")
        .ok_or("graph.json missing `callbot` entry")?;
    let graph_json = serde_json::to_string(graph_v)?;

    let inputs_obj = inputs_bundle
        .get("callbot")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();

    let engine = Operon::builder(&graph_json).auto_register().build()?;
    match engine.run_json(inputs_obj, None, None, None) {
        Ok(r) => println!("[callbot] {r}"),
        Err(e) => println!("[callbot] error: {e}"),
    }

    Ok(())
}
