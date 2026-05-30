//! Verify the scheduler's Ref evaluator transforms match Python semantics.
//!
//! Stage 3 of the operonx Rust sync. Python `Ref` has 30 transform variants
//! ([operonx/core/states/ref.py](../../../../../operonx/core/states/ref.py));
//! the Rust scheduler implements the 26 that can cross the JSON wire format
//! and emits clean errors for the 4 that cannot (`apply`, `call`, `matmul`,
//! `rmatmul` — all of which Python's `_serialize_transforms` either refuses
//! to serialize or that require non-JSON value shapes).
//!
//! This test file exercises the variants we want pinned for parity:
//!
//! - `getitem` on `Value::Object` and `Value::Array` — unchanged from the
//!   pre-Stage-3 behavior; regression coverage.
//! - `getattr` on `Value::Object` — matches Python's `getattr(dict, name)`,
//!   distinct from the pre-Stage-3 alias to `getitem`.
//! - `getattr` on non-Object — Python raises AttributeError; Rust now errors
//!   with a matching message instead of silently returning Null.
//! - `apply` / `call` / `matmul` / `rmatmul` — Python refuses these at
//!   serialization; Rust errors at evaluation with a message pointing the
//!   user at `@op(rust='...')`.

use operonx::Operon;
use serde_json::{json, Map, Value};

/// Build a two-op graph where `producer` emits an object, then `consumer`
/// reads a single field from it via a ref carrying `transform` (e.g.
/// `["getattr", ["k"]]`). `consumer` returns `{"got": <transformed value>}`
/// so the test can read the result back from the engine's output dict.
fn ref_probe_graph(transform: Value, _producer_output: Value) -> String {
    // Mirrors the shape of `tests/spec/core/scheduler/two_op_chain/graph.json`:
    // `producer` returns `{out: <object>}`; `consumer` takes input via a ref
    // `[producer.out, transforms=[<transform>]]`, returns `{got: x}` which is
    // forwarded to the main graph's `got` output slot via a `__PARENT__` ref.
    json!({
        "schema_version": "1.0",
        "type": "graph",
        "name": "main",
        "full_name": "main",
        "entries": ["producer"],
        "exits": ["consumer"],
        "initial_ready_count": {"producer": 0, "consumer": 1},
        "compiled_adj": {
            "producer": [["consumer", false]],
            "consumer": []
        },
        "inputs": {},
        "outputs": {"got": {}, "error": {}},
        "ops": {
            "producer": {
                "type": "code",
                "name": "producer",
                "full_name": "main.producer",
                "func_name": "produce",
                "bound": "sync",
                "inputs": {},
                "outputs": {"out": {}}
            },
            "consumer": {
                "type": "code",
                "name": "consumer",
                "full_name": "main.consumer",
                "func_name": "consume",
                "bound": "sync",
                "inputs": {
                    "x": {
                        "required": true,
                        "ref": {
                            "source": "main.producer",
                            "var": "out",
                            "transforms": [transform]
                        }
                    }
                },
                "outputs": {
                    "got": {
                        "ref": {
                            "source": "__PARENT__",
                            "var": "got",
                            "is_output": true
                        }
                    },
                    "error": {
                        "ref": {
                            "source": "__PARENT__",
                            "var": "error",
                            "is_output": true
                        }
                    }
                }
            }
        }
    })
    .to_string()
}

/// Drive the probe graph and return the engine's output map — or the
/// rendered error string for negative-case tests.
async fn run_probe(transform: Value, producer_output: Value) -> Result<Value, String> {
    let graph = ref_probe_graph(transform, producer_output.clone());
    let producer_out_clone = producer_output.clone();
    let engine = Operon::builder(&graph)
        .no_resources()
        .install_global_hub(false)
        .load_dotenv(false)
        .op("produce", move |_inputs: Map<String, Value>| {
            Ok(json!({ "out": producer_out_clone.clone() }))
        })
        .op("consume", |inputs: Map<String, Value>| {
            let x = inputs.get("x").cloned().unwrap_or(Value::Null);
            Ok(json!({ "got": x }))
        })
        .build()
        .map_err(|e| e.to_string())?;

    let out = engine
        .run_json_async(Map::new(), None, None, None)
        .await
        .map_err(|e| e.to_string())?;

    // Ref-eval failures inside an op are surfaced as `{error: "..."}` frames,
    // not as `Err` from the engine. The probe graph forwards consumer's
    // `error` output to PARENT so this helper can re-raise it as Err for
    // negative-case tests.
    if let Some(err) = out.get("error") {
        if !err.is_null() {
            return Err(err.as_str().map(String::from).unwrap_or_else(|| err.to_string()));
        }
    }
    Ok(out)
}

// ── getitem on Object / Array (regression — must keep working) ────────

#[tokio::test]
async fn getitem_on_object_returns_key() {
    let out = run_probe(
        json!(["getitem", ["name"]]),
        json!({"name": "alice", "age": 30}),
    )
    .await
    .expect("run");
    assert_eq!(out["got"], json!("alice"));
}

#[tokio::test]
async fn getitem_on_array_returns_index() {
    let out = run_probe(json!(["getitem", [1]]), json!(["a", "b", "c"]))
        .await
        .expect("run");
    assert_eq!(out["got"], json!("b"));
}

#[tokio::test]
async fn getitem_negative_index_wraps_python_style() {
    let out = run_probe(json!(["getitem", [-1]]), json!(["a", "b", "c"]))
        .await
        .expect("run");
    assert_eq!(out["got"], json!("c"));
}

// ── getattr on Object (parity with getitem for dicts) ─────────────────

#[tokio::test]
async fn getattr_on_object_returns_key() {
    let out = run_probe(
        json!(["getattr", ["name"]]),
        json!({"name": "alice", "age": 30}),
    )
    .await
    .expect("run");
    assert_eq!(out["got"], json!("alice"));
}

#[tokio::test]
async fn getattr_on_object_missing_key_returns_null() {
    // Parity choice: Python `getattr(dict, missing)` raises AttributeError,
    // but the JSON-Object equivalent is `dict.get(key, None)`. Rust returns
    // Null on miss — consistent with `getitem` and with how typical
    // serialized graphs handle optional fields.
    let out = run_probe(json!(["getattr", ["nope"]]), json!({"name": "alice"}))
        .await
        .expect("run");
    assert_eq!(out["got"], json!(null));
}

// ── getattr on non-Object errors (the Python AttributeError parity) ──

#[tokio::test]
async fn getattr_on_string_errors_with_attribute_error_message() {
    let err = run_probe(json!(["getattr", ["upper"]]), json!("hello"))
        .await
        .unwrap_err();
    assert!(
        err.contains("AttributeError"),
        "want AttributeError, got: {err}"
    );
    assert!(err.contains("'str'"), "want 'str' type, got: {err}");
    assert!(err.contains("upper"), "want attr name in error, got: {err}");
}

#[tokio::test]
async fn getattr_on_int_errors_with_attribute_error_message() {
    let err = run_probe(json!(["getattr", ["bit_length"]]), json!(42))
        .await
        .unwrap_err();
    assert!(
        err.contains("AttributeError"),
        "want AttributeError, got: {err}"
    );
    assert!(
        err.contains("'number'"),
        "want 'number' type, got: {err}"
    );
}

// ── Unsupported transforms: apply / call / matmul / rmatmul ──────────

#[tokio::test]
async fn apply_errors_with_named_message() {
    let err = run_probe(
        json!(["apply", ["some_fn", [], {}]]),
        json!({"k": "v"}),
    )
    .await
    .unwrap_err();
    assert!(
        err.contains("apply"),
        "want 'apply' transform name in error, got: {err}"
    );
    assert!(
        err.contains("not supported"),
        "want 'not supported' in error, got: {err}"
    );
    assert!(
        err.contains("@op"),
        "want migration hint pointing at @op, got: {err}"
    );
}

#[tokio::test]
async fn call_errors_with_named_message() {
    let err = run_probe(json!(["call", [[], {}]]), json!({"k": "v"}))
        .await
        .unwrap_err();
    assert!(err.contains("call"), "want 'call' transform name in error, got: {err}");
    assert!(
        err.contains("not supported"),
        "want 'not supported' in error, got: {err}"
    );
}

#[tokio::test]
async fn matmul_errors_with_named_message() {
    let err = run_probe(json!(["matmul", [[1, 0]]]), json!([1, 2]))
        .await
        .unwrap_err();
    assert!(
        err.contains("matmul"),
        "want 'matmul' transform name in error, got: {err}"
    );
    assert!(
        err.contains("not supported"),
        "want 'not supported' in error, got: {err}"
    );
}

#[tokio::test]
async fn rmatmul_errors_with_named_message() {
    let err = run_probe(json!(["rmatmul", [[1, 0]]]), json!([1, 2]))
        .await
        .unwrap_err();
    assert!(
        err.contains("rmatmul"),
        "want 'rmatmul' transform name in error, got: {err}"
    );
}
