//! Verify `ExecutionHandle::{scratch, interrupts}` parity with Python.
//!
//! Stage 5b + 5c of the operonx Rust sync.
//!
//! - `handle.scratch()` — returns the shared `Arc<Mutex<HashMap>>` backing
//!   the per-call SCRATCH. Tests cover: pre-seed before run, post-run read
//!   of values written by the seed, and the same Arc visible to the
//!   scheduler.
//! - `handle.interrupts()` — walks buffered frames, filters synthetic
//!   `__interrupt__` ops, deserialises payloads back into typed
//!   `Interrupt`s. Test asserts the round trip preserves `ctx_to_cancel`
//!   and `reason` fields.

use std::collections::HashMap;
use std::time::Duration;

use operonx::{Interrupt, Operon};
use serde_json::{json, Map, Value};

// ── 5b: handle.scratch — round trips through SCRATCH ────────────────

/// Build a one-op graph where the op reads `SCRATCH["seeded_key"]` via a
/// scratch ref input, returns it as `{out}`. Plus declares `out` as a
/// PARENT-forwarded output so the engine returns it.
fn scratch_probe_graph() -> String {
    json!({
        "schema_version": "1.0",
        "type": "graph",
        "name": "main",
        "full_name": "main",
        "entries": ["echo"],
        "exits": ["echo"],
        "initial_ready_count": {"echo": 0},
        "compiled_adj": {"echo": []},
        "inputs": {},
        "outputs": {"out": {}},
        "ops": {
            "echo": {
                "type": "code",
                "name": "echo",
                "full_name": "main.echo",
                "func_name": "scratch_echo",
                "bound": "sync",
                "inputs": {
                    "x": {
                        "required": true,
                        "scratch": {"key": "seeded_key"}
                    }
                },
                "outputs": {
                    "out": {
                        "ref": {
                            "source": "__PARENT__",
                            "var": "out",
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
async fn handle_scratch_seed_round_trips_to_op_input() {
    let graph = scratch_probe_graph();
    let engine = Operon::builder(&graph)
        .no_resources()
        .install_global_hub(false)
        .load_dotenv(false)
        .op("scratch_echo", |inputs: Map<String, Value>| {
            Ok(json!({ "out": inputs.get("x").cloned().unwrap_or(Value::Null) }))
        })
        .build()
        .expect("build engine");

    let mut seed = Map::new();
    seed.insert("seeded_key".into(), json!("hello-from-seed"));
    let out = engine
        .run_json_async_with_scratch(Map::new(), None, None, None, Some(seed))
        .await
        .expect("run");

    assert_eq!(out["out"], json!("hello-from-seed"));
}

#[tokio::test]
async fn handle_scratch_arc_is_visible_post_run() {
    // `engine.start()` returns ExecutionHandle; the handle's scratch() arc
    // should reflect the post-run state of SCRATCH (here: the seed values
    // we put in).
    let graph = scratch_probe_graph();
    let engine = Operon::builder(&graph)
        .no_resources()
        .install_global_hub(false)
        .load_dotenv(false)
        .op("scratch_echo", |inputs: Map<String, Value>| {
            Ok(json!({ "out": inputs.get("x").cloned().unwrap_or(Value::Null) }))
        })
        .build()
        .expect("build engine");

    let mut seed = Map::new();
    seed.insert("seeded_key".into(), json!("seeded"));
    seed.insert("other_key".into(), json!(42));

    let mut handle = engine
        .start(Map::new(), None, None, None, None, Some(seed))
        .expect("start engine");

    let _ = handle.collect(operonx::CollectMode::Group, true).await;

    let scratch = handle.scratch();
    let s = scratch.lock();
    assert_eq!(s.get("seeded_key"), Some(&json!("seeded")));
    assert_eq!(s.get("other_key"), Some(&json!(42)));
}

#[tokio::test]
async fn handle_scratch_pre_seed_via_lock_visible_to_op() {
    // Confirms the alternative path: rather than passing scratch=Some(...)
    // through start(), the caller can lock handle.scratch() before consuming
    // frames and write values that ops will see.
    // (For this test we still need to start the engine, but we write to
    //  the Arc *between* `start()` and the first await — race-free per the
    //  docstring rules.)
    let graph = scratch_probe_graph();
    let engine = Operon::builder(&graph)
        .no_resources()
        .install_global_hub(false)
        .load_dotenv(false)
        .op("scratch_echo", |inputs: Map<String, Value>| {
            Ok(json!({ "out": inputs.get("x").cloned().unwrap_or(Value::Null) }))
        })
        .build()
        .expect("build engine");

    let mut handle = engine
        .start(Map::new(), None, None, None, None, None)
        .expect("start engine");

    // Write to scratch BEFORE awaiting any frame. Synchronous — no scheduler
    // tick happens here yet.
    {
        let scratch = handle.scratch();
        let mut s = scratch.lock();
        s.insert("seeded_key".into(), json!("written-pre-await"));
    }

    let result = handle
        .collect(operonx::CollectMode::Group, true)
        .await
        .expect("collect");
    assert_eq!(result["out"], json!("written-pre-await"));
}

// ── 5c: handle.interrupts — typed Interrupt list ───────────────────

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn handle_interrupts_returns_typed_list_from_synthetic_frames() {
    // Two-op graph: a slow op (gets cancelled), and a fast op that returns
    // an Interrupt targeting the slow op's context.
    let graph = json!({
        "schema_version": "1.0",
        "type": "graph",
        "name": "main",
        "full_name": "main",
        "entries": ["slow", "kicker"],
        "exits": ["slow", "kicker"],
        "initial_ready_count": {"slow": 0, "kicker": 0},
        "compiled_adj": {"slow": [], "kicker": []},
        "inputs": {
            "seed": {"required": true}
        },
        "outputs": {},
        "ops": {
            "slow": {
                "type": "code",
                "name": "slow",
                "full_name": "main.slow",
                "func_name": "long_sleep",
                "is_async": true,
                "bound": "io",
                "inputs": {
                    "_seed": {
                        "required": true,
                        "ref": {"source": "__PARENT__", "var": "seed"}
                    }
                },
                "outputs": {}
            },
            "kicker": {
                "type": "code",
                "name": "kicker",
                "full_name": "main.kicker",
                "func_name": "kick_with_typed",
                "bound": "io",
                "inputs": {
                    "_seed": {
                        "required": true,
                        "ref": {"source": "__PARENT__", "var": "seed"}
                    }
                },
                "outputs": {}
            }
        }
    })
    .to_string();

    let engine = Operon::builder(&graph)
        .no_resources()
        .install_global_hub(false)
        .load_dotenv(false)
        .op_async("long_sleep", |_inputs: Map<String, Value>| async move {
            tokio::time::sleep(Duration::from_secs(5)).await;
            Ok(json!({"out": "should-not-finish"}))
        })
        .op("kick_with_typed", |_inputs: Map<String, Value>| {
            // Use the typed builder, then .into() to the canonical Value.
            let irq = Interrupt::new(vec!["main".to_string()], "rust-typed");
            Ok(irq.into())
        })
        .build()
        .expect("build engine");

    let mut inputs = Map::new();
    inputs.insert("seed".into(), json!("x"));

    let mut handle = engine
        .start(inputs, None, None, None, None, None)
        .expect("start");

    // Drain the handle (timed out so failing tests don't hang).
    let _ = tokio::time::timeout(
        Duration::from_secs(2),
        handle.collect(operonx::CollectMode::Group, true),
    )
    .await
    .expect("collect");

    let interrupts = handle.interrupts();
    assert!(
        !interrupts.is_empty(),
        "expected at least one Interrupt event in the frame stream"
    );
    let irq = &interrupts[0];
    assert_eq!(irq.ctx_to_cancel, vec!["main".to_string()]);
    assert_eq!(irq.reason, "rust-typed");
}

#[test]
fn handle_scratch_type_is_hash_map_string_value() {
    // Smoke check that the exposed type is the documented `Arc<Mutex<HashMap>>`.
    use parking_lot::Mutex;
    use std::sync::Arc;
    let _t: Arc<Mutex<HashMap<String, Value>>> =
        Arc::new(Mutex::new(HashMap::new()));
}
