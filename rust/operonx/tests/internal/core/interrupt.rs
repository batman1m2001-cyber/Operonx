//! Verify the scheduler honours the `Interrupt` event.
//!
//! Mirrors a subset of Python's Phase B test_interrupt.py — focused on the
//! observable behaviour: an op returning the synthetic
//! `{"__interrupt__": {...}}` shape causes the scheduler to abort other
//! in-flight tasks at the target ctx, and the run completes promptly.

use std::time::Duration;

use operonx::Operon;
use serde_json::{json, Map, Value};

/// Build a parallel two-branch graph: `[long_sleep, kick_interrupt]`,
/// both children of START, both feeding END. Uses `op_async` for the
/// long-sleep branch so cancellation can actually propagate via
/// `JoinHandle::abort()`.
fn parallel_graph_json() -> String {
    json!({
        "type": "graph",
        "name": "main",
        "full_name": "main",
        "func_name": null,
        "is_async": false,
        "is_generator": false,
        "enabled": true,
        "verbose": false,
        "stream": false,
        "bound": "sync",
        "inputs": {
            "seed": {
                "default": null, "required": true,
                "ref": null, "scratch": null, "literal": null
            }
        },
        "outputs": {},
        "ops": {
            "slow": {
                "type": "code",
                "name": "slow",
                "full_name": "main.slow",
                "func_name": "long_sleep",
                "is_async": true,
                "is_generator": false,
                "enabled": true,
                "verbose": false,
                "stream": false,
                "bound": "io",
                "inputs": {
                    "_seed": {
                        "default": null, "required": true,
                        "ref": {"source": "__PARENT__", "var": "seed",
                                "transforms": [], "is_output": false},
                        "scratch": null, "literal": null
                    }
                },
                "outputs": {}
            },
            "kicker": {
                "type": "code",
                "name": "kicker",
                "full_name": "main.kicker",
                "func_name": "kick_interrupt",
                "is_async": false,
                "is_generator": false,
                "enabled": true,
                "verbose": false,
                "stream": false,
                "bound": "io",
                "inputs": {
                    "_seed": {
                        "default": null, "required": true,
                        "ref": {"source": "__PARENT__", "var": "seed",
                                "transforms": [], "is_output": false},
                        "scratch": null, "literal": null
                    }
                },
                "outputs": {}
            }
        },
        "edges": [],
        "entries": ["slow", "kicker"],
        "exits": ["slow", "kicker"],
        "initial_ready_count": {"slow": 0, "kicker": 0},
        "compiled_adj": {"slow": [], "kicker": []},
        "stream_initial_ready": {},
        "loop_config": null,
        "max_stream_concurrent": 64,
        "schema_version": "1.0"
    })
    .to_string()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn interrupt_aborts_in_flight_task() {
    let graph = parallel_graph_json();
    let engine = Operon::builder(&graph)
        .no_resources()
        .install_global_hub(false)
        .load_dotenv(false)
        .op_async("long_sleep", |_inputs: Map<String, Value>| async move {
            // Would sleep 5s if not aborted. Cancellation propagates via
            // tokio::time::sleep — the JoinHandle::abort() raises
            // CancelledError at the await point inside the spawned task.
            tokio::time::sleep(Duration::from_secs(5)).await;
            Ok(json!({ "out": "completed" }))
        })
        .op("kick_interrupt", |_inputs: Map<String, Value>| {
            // Sync op — returns the synthetic shape that the scheduler
            // recognises and turns into a SchedulerEvent::Interrupt.
            Ok(json!({
                "__interrupt__": {
                    "ctx_to_cancel": ["main"],
                    "reason": "test"
                }
            }))
        })
        .build()
        .expect("build engine");

    let mut inputs = Map::new();
    inputs.insert("seed".into(), json!("x"));

    let started = std::time::Instant::now();
    let _ = tokio::time::timeout(
        Duration::from_secs(2),
        engine.run_json_async(inputs, None, None, None),
    )
    .await
    .expect("scheduler should complete within budget");
    let elapsed = started.elapsed();
    assert!(
        elapsed < Duration::from_secs(2),
        "expected sweep to abort long_sleep promptly; took {:?}",
        elapsed
    );
}
