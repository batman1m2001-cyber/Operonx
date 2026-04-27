//! Verify the scheduler honours `RefConfig.stream_policy`.
//!
//! Python's `Ref.serialize()` does not yet emit stream_policy (tracked as a
//! known gap — see `core/states/ref.rs`). Rust-native callers set it
//! directly on graph JSON. These tests drive the scheduler with the policy
//! fields filled in manually and assert the expected collect/parallel
//! behaviour.

use operonx::{op, Operon};
use serde_json::{json, Map, Value};

/// Source op — echoes its `items` input as-is so we can inspect the shape
/// of the downstream's input map.
#[op(name = "stream_source")]
fn stream_source(items: Value) -> Value {
    json!({ "item": items })
}

/// Sink — records whatever it received under `saw`.
#[op(name = "stream_sink")]
fn stream_sink(item: Value) -> Value {
    json!({ "saw": item })
}

fn graph_with_policy(collect: bool, parallel: bool) -> String {
    let mut stream_policy = Map::new();
    stream_policy.insert("collect".into(), Value::Bool(collect));
    stream_policy.insert("parallel".into(), Value::Bool(parallel));
    stream_policy.insert("parallel_max".into(), Value::from(0));

    let ref_obj = json!({
        "source": "main.source",
        "var": "item",
        "stream_policy": stream_policy
    });

    json!({
        "schema_version": "1.0",
        "type": "graph",
        "name": "main",
        "full_name": "main",
        "entries": ["source"],
        "exits": ["sink"],
        "initial_ready_count": {"source": 0, "sink": 1},
        "compiled_adj": {
            "source":  [["sink", false]],
            "sink": []
        },
        "inputs": {"items": {"required": true}},
        "outputs": {"saw": {}},
        "ops": {
            "source": {
                "type": "code",
                "name": "source",
                "full_name": "main.source",
                "func_name": "stream_source",
                "bound": "sync",
                "inputs": {
                    "items": {
                        "required": true,
                        "ref": {"source": "__PARENT__", "var": "items"}
                    }
                },
                "outputs": {"item": {}}
            },
            "sink": {
                "type": "code",
                "name": "sink",
                "full_name": "main.sink",
                "func_name": "stream_sink",
                "bound": "sync",
                "inputs": {
                    "item": {
                        "required": true,
                        "ref": ref_obj
                    }
                },
                "outputs": {
                    "saw": {
                        "ref": {
                            "source": "__PARENT__",
                            "var": "saw",
                            "is_output": true
                        }
                    }
                }
            }
        }
    })
    .to_string()
}

#[tokio::test]
async fn collect_policy_merges_frames_then_dispatches_once() {
    let graph = graph_with_policy(true, false);
    let engine = Operon::builder(&graph)
        .no_resources()
        .install_global_hub(false)
        .load_dotenv(false)
        .auto_register()
        .build()
        .expect("engine builds");

    let mut inputs = Map::new();
    inputs.insert("items".into(), json!([1, 2, 3]));

    let out = engine
        .run_json_async(inputs, None, None, None)
        .await
        .unwrap();
    // `stream_source` emits one frame `{item: [1,2,3]}`; with `collect` the
    // scheduler wraps it into a list of length 1 then dispatches sink once.
    // Sink echoes its `item` back under `saw` — we assert `saw` is the
    // collected list shape.
    let saw = out.get("saw").expect("saw present");
    assert!(saw.is_array(), "saw should be a list (collect merged)");
    assert_eq!(saw.as_array().unwrap().len(), 1);
}

#[tokio::test]
async fn parallel_policy_routes_immediately() {
    // With only one source frame the behaviour is indistinguishable from
    // sequential, so we just assert the happy-path still works — the real
    // concurrency property is covered by the semaphore contract and
    // exercising it requires a generator op (Phase 6+).
    let graph = graph_with_policy(false, true);
    let engine = Operon::builder(&graph)
        .no_resources()
        .install_global_hub(false)
        .load_dotenv(false)
        .auto_register()
        .build()
        .unwrap();

    let mut inputs = Map::new();
    inputs.insert("items".into(), json!("hello"));
    let out = engine
        .run_json_async(inputs, None, None, None)
        .await
        .unwrap();
    assert_eq!(out.get("saw").and_then(|v| v.as_str()), Some("hello"));
}
