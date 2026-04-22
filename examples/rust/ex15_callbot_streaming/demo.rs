//! 15 Callbot Streaming — Rust-side usage demo.
//!
//! Mirrors `examples/python/ex15_callbot_streaming/workflow.py`.
//!
//! TODO: the callbot is almost entirely generator + nested `@graph`, both
//! of which are Rust-limited. The ops are declared here for parity; the
//! Rust run will short-circuit at the generator and the nested llm_router
//! graph.

use operonx::op;
use serde_json::{json, Value};

#[op(name = "customer_audio")]
fn customer_audio(sample_count: i64) -> Value {
    let out: Vec<Value> = (0..sample_count)
        .map(|i| json!({ "audio": format!("chunk_{}", i), "timestamp_ms": i * 32 }))
        .collect();
    json!({ "items": out })
}

#[op(name = "vad")]
fn vad(audio: String, timestamp_ms: i64) -> Value {
    let speech_timestamps = [64_i64, 128_i64];
    if speech_timestamps.contains(&timestamp_ms) {
        json!({
            "segment": format!("speech_from_{}", audio),
            "start_ms": timestamp_ms,
            "end_ms": timestamp_ms + 32,
        })
    } else {
        // Silence — yields nothing in Python; return nulls here.
        json!({
            "segment": Value::Null,
            "start_ms": Value::Null,
            "end_ms": Value::Null,
        })
    }
}

#[op(name = "stt")]
fn stt(segment: String, start_ms: i64, end_ms: i64) -> Value {
    json!({
        "transcript": format!("Hello from {} [{}-{}ms]", segment, start_ms, end_ms),
    })
}

#[op(name = "classify_intent")]
fn classify_intent(transcript: String) -> Value {
    if transcript.to_lowercase().contains("hello") {
        json!({ "intent": "greeting", "confidence": 0.95 })
    } else {
        json!({ "intent": "general", "confidence": 0.8 })
    }
}

#[op(name = "handle_intent")]
fn handle_intent(intent: String, transcript: String) -> Value {
    if intent == "greeting" {
        json!({ "response": "Hello! How can I help you today?" })
    } else {
        json!({ "response": format!("I understand. Let me help with: {}", transcript) })
    }
}

#[op(name = "tts")]
fn tts(response: String) -> Value {
    let words: Vec<&str> = response.split_whitespace().collect();
    let out: Vec<Value> = words
        .into_iter()
        .enumerate()
        .map(|(i, word)| json!({ "audio_out": format!("tts_{}_{}", i, word), "index": i }))
        .collect();
    json!({ "items": out })
}

#[path = "../_common.rs"]
mod common;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let example = "ex15_callbot_streaming";
    let args = common::parse_args(example);

    let graph_bundle = common::load_json(example, "graph.json")?;
    let inputs_bundle = common::load_json(example, "inputs.json")?;

    // TODO: Rust-limited — relies on generator per-item dispatch, nested
    // `@graph` composition, and streaming.
    let scenarios = ["callbot"];
    let mut reporter = common::BenchReporter::new(example);

    for name in scenarios {
        let graph_v = graph_bundle
            .get(name)
            .ok_or_else(|| format!("graph.json missing `{}` entry", name))?
            .clone();
        let graph_v = common::rename_graph(graph_v, "_rust");
        let graph_json = serde_json::to_string(&graph_v)?;

        let inputs_obj = inputs_bundle
            .get(name)
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();

        let engine = common::build_engine(&graph_json, &args)?;
        reporter.record(name, args.runs, || {
            let out = engine.run_json(inputs_obj.clone(), None, None, None)?;
            Ok(out)
        })?;
    }

    reporter.save()?;
    Ok(())
}
