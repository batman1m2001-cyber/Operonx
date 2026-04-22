//! Phase 9 parity harness.
//!
//! Drives shared JSON fixtures through the Rust engine. Each fixture folder
//! under `tests/spec/<area>/<name>/` contains `graph.json` + `inputs.json` +
//! `expected.json`. Rust reads the same files Python does, runs the graph,
//! then compares the output against `expected.json` after stripping timing
//! keys (`$start_time` / `$end_time` / `$duration_ms`) which are inherently
//! non-deterministic run-to-run.
//!
//! A pool of test ops — `double`, `add_one`, `echo`, `wrap_user`,
//! `choose_branch`, `each_item`, `accumulate` — is registered via `#[op]`
//! and picked up by `OperonBuilder::auto_register()`. Fixture graphs
//! reference these by short name.

#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::sync::Once;

use operonx::{op, Operon};
use serde_json::Value;

/// Resolve a fixture path rooted at the repo-root `Operon/tests/spec/`.
///
/// Dev path: `CARGO_MANIFEST_DIR/../../tests/spec/<name>/…` — both Python
/// (`Operon/tests/spec/`) and Rust read from here so the JSON fixtures are
/// the single source of truth per plan §8.5.
///
/// Publish fallback: when the crate is consumed from `crates.io`, the
/// `../../tests/spec` relative hop breaks; the bundled copy under
/// `CARGO_MANIFEST_DIR/tests/spec/` (populated by the crate's packaging
/// step) is used instead. Tried in order.
pub fn fixture_path(name: &str) -> PathBuf {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let repo_root = manifest.join("..").join("..").join("tests").join("spec").join(name);
    if repo_root.exists() {
        return repo_root;
    }
    manifest.join("tests").join("spec").join(name)
}

/// Read + parse one JSON file from the harness-rooted fixture tree.
pub fn load_json(path: &Path) -> Value {
    let txt = std::fs::read_to_string(path)
        .unwrap_or_else(|e| panic!("failed to read {}: {}", path.display(), e));
    serde_json::from_str(&txt)
        .unwrap_or_else(|e| panic!("invalid JSON in {}: {}", path.display(), e))
}

/// Run a fixture end-to-end and assert its output matches `expected.json`.
///
/// `name` is the path under `tests/spec/`, e.g.
/// `"core/scheduler/single_code_op"`.
pub async fn run_fixture(name: &str) {
    ensure_test_setup();

    let dir = fixture_path(name);
    let graph = load_json(&dir.join("graph.json"));
    let inputs = load_json(&dir.join("inputs.json"));
    let expected = load_json(&dir.join("expected.json"));

    let graph_str = serde_json::to_string(&graph).expect("serialize graph");
    let engine = Operon::builder(&graph_str)
        .no_resources()
        .install_global_hub(false)
        .load_dotenv(false)
        .auto_register()
        .build()
        .unwrap_or_else(|e| panic!("fixture '{}' build failed: {}", name, e));

    let inputs_map = inputs.as_object().cloned().unwrap_or_default();
    let got = engine
        .run_json_async(inputs_map, None, None, None)
        .await
        .unwrap_or_else(|e| panic!("fixture '{}' runtime error: {}", name, e));

    assert_eq_ignoring_timing(&got, &expected, name);
}

/// Compare two JSON values, ignoring the well-known timing keys the engine
/// attaches to every op's state (`$start_time`, `$end_time`, `$duration_ms`).
/// Panics with a readable diff when they differ.
pub fn assert_eq_ignoring_timing(got: &Value, expected: &Value, ctx: &str) {
    let got_s = strip_timing(got.clone());
    let exp_s = strip_timing(expected.clone());
    if got_s != exp_s {
        panic!(
            "fixture '{}' output mismatch\n  got:      {}\n  expected: {}",
            ctx,
            serde_json::to_string_pretty(&got_s).unwrap(),
            serde_json::to_string_pretty(&exp_s).unwrap(),
        );
    }
}

/// Recursively remove every `$start_time` / `$end_time` / `$duration_ms` key
/// from the tree. Anything else survives — including business-logic keys
/// with literal dollar prefixes (none currently, but harmless either way).
pub fn strip_timing(v: Value) -> Value {
    match v {
        Value::Object(m) => Value::Object(
            m.into_iter()
                .filter(|(k, _)| !is_timing_key(k))
                .map(|(k, v)| (k, strip_timing(v)))
                .collect(),
        ),
        Value::Array(arr) => Value::Array(arr.into_iter().map(strip_timing).collect()),
        other => other,
    }
}

fn is_timing_key(k: &str) -> bool {
    matches!(k, "$start_time" | "$end_time" | "$duration_ms")
}

// ── Shared op pool — registered once via #[op] + auto_register ────────────

/// Double an integer input.
#[op(name = "double")]
fn double(x: i64) -> Value {
    serde_json::json!({ "result": x * 2 })
}

/// Add one to `n`.
#[op(name = "add_one")]
fn add_one(n: i64) -> Value {
    serde_json::json!({ "answer": n + 1 })
}

/// Identity — echo inputs back under `output`.
#[op(name = "echo")]
fn echo(msg: Value) -> Value {
    serde_json::json!({ "output": msg })
}

/// Build a `{role: user, content: ...}` message from `text`.
#[op(name = "wrap_user")]
fn wrap_user(text: String) -> Value {
    serde_json::json!({ "msg": { "role": "user", "content": text } })
}

/// Return `{"label": "big"}` when `n > threshold`, else `{"label": "small"}`.
#[op(name = "classify_size")]
fn classify_size(n: i64, threshold: i64) -> Value {
    let label = if n > threshold { "big" } else { "small" };
    serde_json::json!({ "label": label })
}

/// Lowercase wrap of a string — exercises typed-serde-wrapper with String.
#[op(name = "lowercase")]
fn lowercase(text: String) -> Value {
    serde_json::json!({ "result": text.to_lowercase() })
}

/// Sum an array of integers.
#[op(name = "sum_list")]
fn sum_list(xs: Vec<i64>) -> Value {
    serde_json::json!({ "total": xs.iter().sum::<i64>() })
}

/// Increment a counter — drives the `loop_counter_until` fixture.
#[op(name = "increment")]
fn increment(counter: i64) -> Value {
    serde_json::json!({ "counter": counter + 1 })
}

// ── One-time setup ────────────────────────────────────────────────────────

static INIT: Once = Once::new();

/// One-time test init — mostly a noop, kept for future env setup.
fn ensure_test_setup() {
    INIT.call_once(|| {
        // Future: tracing subscriber, env-var wipes, etc. Nothing needed yet.
    });
}

// ── Self-tests — verify the harness itself works ─────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn strip_timing_removes_dollar_keys() {
        let v = json!({
            "result": 10,
            "$start_time": 1.0,
            "$end_time": 2.0,
            "$duration_ms": 1000,
            "nested": {
                "$start_time": 3.0,
                "value": "ok"
            }
        });
        let stripped = strip_timing(v);
        assert_eq!(stripped, json!({"result": 10, "nested": {"value": "ok"}}));
    }

    #[test]
    fn strip_timing_preserves_non_timing_keys() {
        let v = json!({"$custom": "keep-me", "$duration_ms": 5});
        let stripped = strip_timing(v);
        assert_eq!(stripped, json!({"$custom": "keep-me"}));
    }
}
