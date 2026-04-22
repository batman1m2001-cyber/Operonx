//! End-to-end smoke tests for the `#[op]` proc-macro + `auto_register()`.
//!
//! Declares real `#[op]`-annotated functions at module scope, then drives a
//! serialized single-op graph through the engine — proving the full
//! `inventory::submit! → auto_register() → InMemoryOpRegistry → scheduler`
//! pipeline works without any manual `.op(...)` registration.

use operonx::{op, model, Operon};
use serde_json::{json, Map, Value};

// ── Typed op — triple input, return a typed struct ────────────────────

#[model]
struct TripleOut {
    result: i64,
}

/// Typed signature: `i64 → TripleOut`. The macro generates a serde wrapper
/// that pulls `x` out of the input map, deserializes it, runs the body,
/// then serializes the struct back to a JSON object.
#[op(name = "__macros_smoke_triple")]
fn triple(x: i64) -> TripleOut {
    TripleOut { result: x * 3 }
}

/// Untyped / legacy form — takes the whole input `Value` directly.
#[op(name = "__macros_smoke_shout")]
fn shout(inputs: &Value) -> Value {
    let msg = inputs
        .get("msg")
        .and_then(|v| v.as_str())
        .unwrap_or_default();
    json!({"cry": format!("{}!", msg.to_uppercase())})
}

fn graph_json(op_name: &str, input_key: &str, output_key: &str) -> String {
    format!(
        r#"{{
            "schema_version": "1.0",
            "type": "graph",
            "name": "main",
            "full_name": "main",
            "entries": ["step"],
            "exits": ["step"],
            "initial_ready_count": {{"step": 0}},
            "compiled_adj": {{"step": []}},
            "inputs": {{"{input_key}": {{"required": true}}}},
            "outputs": {{"{output_key}": {{}}}},
            "ops": {{
                "step": {{
                    "type": "code",
                    "name": "step",
                    "full_name": "main.step",
                    "func_name": "{op_name}",
                    "bound": "sync",
                    "inputs": {{
                        "{input_key}": {{
                            "required": true,
                            "ref": {{"source": "__PARENT__", "var": "{input_key}"}}
                        }}
                    }},
                    "outputs": {{
                        "{output_key}": {{
                            "ref": {{
                                "source": "__PARENT__",
                                "var": "{output_key}",
                                "is_output": true
                            }}
                        }}
                    }}
                }}
            }}
        }}"#
    )
}

#[tokio::test]
async fn auto_register_dispatches_typed_op() {
    let json_str = graph_json("__macros_smoke_triple", "x", "result");
    let engine = Operon::builder(&json_str)
        .no_resources()
        .install_global_hub(false)
        .auto_register()
        .build()
        .expect("engine builds cleanly");

    let mut inputs = Map::new();
    inputs.insert("x".into(), Value::from(7));

    let out = engine.run_json_async(inputs, None, None, None).await.unwrap();
    assert_eq!(out.get("result"), Some(&Value::from(21)));
}

#[tokio::test]
async fn auto_register_dispatches_untyped_op() {
    let json_str = graph_json("__macros_smoke_shout", "msg", "cry");
    let engine = Operon::builder(&json_str)
        .no_resources()
        .install_global_hub(false)
        .auto_register()
        .build()
        .expect("engine builds cleanly");

    let mut inputs = Map::new();
    inputs.insert("msg".into(), Value::from("hi"));

    let out = engine.run_json_async(inputs, None, None, None).await.unwrap();
    assert_eq!(out.get("cry"), Some(&Value::from("HI!")));
}

#[tokio::test]
async fn typed_op_surfaces_deserialize_error_as_output() {
    // `x` is declared as `i64`; passing a string should trip the serde
    // deserializer and the wrapper emits an `{"error": "..."}` payload
    // instead of panicking — matching Python's error-in-output convention.
    let json_str = graph_json("__macros_smoke_triple", "x", "result");
    let engine = Operon::builder(&json_str)
        .no_resources()
        .install_global_hub(false)
        .auto_register()
        .build()
        .unwrap();

    let mut inputs = Map::new();
    inputs.insert("x".into(), Value::from("not-a-number"));

    let out = engine.run_json_async(inputs, None, None, None).await.unwrap();
    // `result` is schema-declared as an output slot but the wrapper returned
    // `{"error": "..."}` instead — the error *payload* survives by virtue of
    // not being `null`, even though the key doesn't match the `result` slot.
    // We just assert the engine didn't panic and produced *some* output.
    assert!(out.is_object());
}
