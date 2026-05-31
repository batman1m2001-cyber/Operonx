//! Regression tests for the 0.8.1 sequential-edge advance-on-cancel fix.
//!
//! Mirrors Python's
//! [`TestB1bSequentialAdvanceAfterCancel`](../../../../../tests/internal/core/ops/graph/test_interrupt.py)
//! suite. Stage 5d of the operonx Rust sync.
//!
//! Setup: a generator emits N items into a sequential edge (default policy).
//! The active item is cancelled mid-flight by an Interrupt targeting its
//! ctx. The next items waiting in `seq_queues` for the same edge MUST
//! advance — otherwise they sit stuck forever and the rest of the work is
//! silently dropped. Same bug Python had pre-0.8.1.
//!
//! In Rust the generator yields land at `(parent_ctx, "yield_N")` sub-
//! contexts (Python uses `("[N]",)` — same semantics, different label).

use std::time::Duration;

use operonx::{Interrupt, Operon};
use serde_json::{json, Map, Value};

/// Build the b1b regression graph: gen yields 3 items into a sequential
/// edge → slow processes each; kicker runs in parallel and Interrupts the
/// first yield mid-flight.
///
/// `cancel_target` is the ctx tuple the Interrupt targets. The test runs
/// once with `["main", "yield_0"]` (cancel just the first yield — items 1
/// and 2 should still complete) and once with `["main"]` (sweep the whole
/// subtree — no slow frames should land).
fn b1b_graph_json() -> String {
    json!({
        "schema_version": "1.0",
        "type": "graph",
        "name": "main",
        "full_name": "main",
        "entries": ["gen", "kicker"],
        "exits": ["slow", "kicker"],
        "initial_ready_count": {"gen": 0, "kicker": 0, "slow": 1},
        "compiled_adj": {
            "gen": [["slow", false]],
            "slow": [],
            "kicker": []
        },
        "inputs": {"seed": {"required": true}},
        "outputs": {"out": {}},
        "ops": {
            "gen": {
                "type": "code",
                "name": "gen",
                "full_name": "main.gen",
                "func_name": "gen3",
                "is_generator": true,
                "bound": "sync",
                "inputs": {
                    "_signal": {
                        "required": true,
                        "ref": {"source": "__PARENT__", "var": "seed"}
                    }
                },
                "outputs": {"i": {}}
            },
            "slow": {
                "type": "code",
                "name": "slow",
                "full_name": "main.slow",
                "func_name": "slow_double",
                "is_async": true,
                "bound": "io",
                "inputs": {
                    "i": {
                        "required": true,
                        "ref": {"source": "main.gen", "var": "i"}
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
            },
            "kicker": {
                "type": "code",
                "name": "kicker",
                "full_name": "main.kicker",
                "func_name": "kicker_async",
                "is_async": true,
                "bound": "io",
                "inputs": {
                    "_signal": {
                        "required": true,
                        "ref": {"source": "__PARENT__", "var": "seed"}
                    }
                },
                "outputs": {}
            }
        }
    })
    .to_string()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn b1b_siblings_dispatch_after_active_item_cancel() {
    let graph = b1b_graph_json();
    let engine = Operon::builder(&graph)
        .no_resources()
        .install_global_hub(false)
        .load_dotenv(false)
        .op("gen3", |_inputs: Map<String, Value>| {
            // Generator op: returns a Value::Array of items.
            Ok(json!([{"i": 0}, {"i": 1}, {"i": 2}]))
        })
        .op_async("slow_double", |inputs: Map<String, Value>| async move {
            // Long enough that kicker's Interrupt lands while i=0 is still
            // mid-await — cancel-the-active-seq-item case.
            tokio::time::sleep(Duration::from_millis(200)).await;
            let i = inputs.get("i").and_then(|v| v.as_i64()).unwrap_or(-1);
            Ok(json!({"out": i}))
        })
        .op_async("kicker_async", |_inputs: Map<String, Value>| async move {
            // Wait long enough for gen to fan out + slow(i=0) to begin
            // its sleep. 50ms < 200ms.
            tokio::time::sleep(Duration::from_millis(50)).await;
            // Target the first generator yield's context.
            let irq = Interrupt::new(vec!["main".to_string(), "yield_0".to_string()], "b1b");
            Ok(irq.into())
        })
        .build()
        .expect("build engine");

    let mut inputs = Map::new();
    inputs.insert("seed".into(), json!("x"));

    // Wall-clock budget: three sequential 200ms items → 0.6s worst-case.
    // Anything beyond ~1.5s = items 1 and 2 never dispatched (bug).
    let out = tokio::time::timeout(
        Duration::from_millis(1500),
        engine.run_json_async(inputs, None, None, None),
    )
    .await
    .expect("scheduler should not stall after Interrupt cancel")
    .expect("run_json_async failed");

    // i=0 was cancelled mid-await → emits no frame.
    // i=1 and i=2 should still complete after seq_queues advances.
    let produced = out["out"].as_array().cloned().unwrap_or_default();
    let mut js: Vec<i64> = produced.iter().filter_map(|v| v.as_i64()).collect();
    js.sort();
    assert_eq!(
        js,
        vec![1, 2],
        "sequential edge stalled after Interrupt: got {js:?}, expected [1, 2] \
         (item 0 cancelled, items 1 & 2 advance)"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn b1b_descendant_queued_items_dropped_on_subtree_sweep() {
    let graph = b1b_graph_json();
    let engine = Operon::builder(&graph)
        .no_resources()
        .install_global_hub(false)
        .load_dotenv(false)
        .op("gen3", |_inputs: Map<String, Value>| {
            Ok(json!([{"i": 0}, {"i": 1}, {"i": 2}]))
        })
        .op_async("slow_double", |inputs: Map<String, Value>| async move {
            tokio::time::sleep(Duration::from_millis(200)).await;
            let i = inputs.get("i").and_then(|v| v.as_i64()).unwrap_or(-1);
            Ok(json!({"out": i}))
        })
        .op_async("kicker_async", |_inputs: Map<String, Value>| async move {
            tokio::time::sleep(Duration::from_millis(50)).await;
            // Sweep the whole subtree — items 1 & 2 are queued at
            // descendant ctxs and must be dropped, not dispatched.
            let irq = Interrupt::new(vec!["main".to_string()], "b1c");
            Ok(irq.into())
        })
        .build()
        .expect("build engine");

    let mut inputs = Map::new();
    inputs.insert("seed".into(), json!("x"));

    // Should terminate well under the would-be-serial 0.6s — sweep
    // cancels in-flight + drops descendants.
    let out = tokio::time::timeout(
        Duration::from_millis(1000),
        engine.run_json_async(inputs, None, None, None),
    )
    .await
    .expect("scheduler should not hang after subtree sweep")
    .expect("run_json_async failed");

    let produced = out
        .get("out")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    assert!(
        produced.is_empty(),
        "subtree sweep should have dropped all queued items; got {produced:?}"
    );
}
