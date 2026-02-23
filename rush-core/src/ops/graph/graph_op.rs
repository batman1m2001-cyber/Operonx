//! Graph op execution — scheduling loop, output collection, nested graphs.
//!
//! Mirrors Python's `ops/graph/graph_op.py` (GraphOp).
//! Supports batch concurrent execution via tokio when multiple independent ops are ready.
//! Uses OpBound to determine scheduling strategy:
//! - I/O-bound ops: tokio async (HTTP calls overlap without blocking threads)
//! - CPU-bound ops: tokio::spawn_blocking (parallel CPU, GIL released independently)

use ahash::{AHashMap, AHashSet};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::config::{GraphConfig, OpBound, OpConfig};
use crate::ops::base;
use crate::ops::iteration::{aiter_op, for_op, map_op, while_op};
use crate::runtime;
use crate::states::state::EngineState;

// =============================================================================
// Op dispatch
// =============================================================================

/// Dispatch an op to the appropriate runner based on op_type.
pub(crate) fn dispatch_op(
    py: Python,
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    match op.op_type.as_str() {
        "graph" => run_nested(py, op, state, context),
        "for" => for_op::run(py, op, state, context),
        "while" => while_op::run(py, op, state, context),
        "map" => map_op::run(py, op, state, context),
        "stream" => aiter_op::run(py, op, state, context),
        _ => base::run(py, op, state, context),
    }
}

// =============================================================================
// Graph scheduling loop
// =============================================================================

/// Run a graph's scheduling loop (used for both top-level and nested graphs).
///
/// Supports two execution modes:
/// - **Sequential** (default): ops executed one at a time (optimal for sync Python ops)
/// - **Concurrent** (batch): independent ops executed via tokio's thread pool
///
/// Concurrent mode activates when a batch has 2+ ops AND at least one has
/// `bound = Io` (I/O-bound, releases GIL during HTTP waits) or a Rust-native
/// op (`rust_op`, executes without GIL). Pure sync CPU-bound Python batches
/// run sequentially to avoid GIL contention overhead.
pub(crate) fn run_graph(
    py: Python,
    config: &GraphConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    let mut ready_count = config.initial_ready_count.clone();
    let mut soft_satisfied: AHashSet<String> = AHashSet::new();
    let mut queue: Vec<String> = config.entries.clone();

    while !queue.is_empty() {
        let batch: Vec<String> = queue.drain(..).collect();

        // Check if concurrent execution would benefit this batch
        let use_concurrent = batch.len() > 1 && batch.iter().any(|name| {
            config.ops.get(name).map_or(false, |op| {
                op.bound == OpBound::Io || op.rust_op.is_some()
            })
        });

        if use_concurrent {
            run_batch(py, &batch, config, state, context)?;

            for op_name in &batch {
                let op = config.ops.get(op_name).ok_or_else(|| {
                    PyErr::new::<pyo3::exceptions::PyKeyError, _>(format!(
                        "Op '{}' not found in config",
                        op_name
                    ))
                })?;
                activate_successors(
                    py, op, op_name, config, state, context,
                    &mut ready_count, &mut soft_satisfied, &mut queue,
                )?;
            }
        } else {
            for op_name in batch {
                let op = config.ops.get(&op_name).ok_or_else(|| {
                    PyErr::new::<pyo3::exceptions::PyKeyError, _>(format!(
                        "Op '{}' not found in config",
                        op_name
                    ))
                })?;

                dispatch_op(py, op, state, context)?;

                activate_successors(
                    py, op, &op_name, config, state, context,
                    &mut ready_count, &mut soft_satisfied, &mut queue,
                )?;
            }
        }
    }

    Ok(())
}

// =============================================================================
// Batch concurrent execution
// =============================================================================

/// Execute a batch of independent ops concurrently via tokio.
///
/// All ops in the batch have ready_count == 0 (no dependencies on each other).
/// Each op is spawned as a `tokio::task::spawn_blocking` task that acquires
/// the GIL independently. DashMap-based EngineState allows concurrent reads/writes.
fn run_batch(
    py: Python,
    batch: &[String],
    config: &GraphConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    let state_addr = state as *const EngineState as usize;
    let context_owned = context.to_string();

    let op_addrs: Vec<usize> = batch
        .iter()
        .filter_map(|name| {
            config.ops.get(name).map(|op| op as *const OpConfig as usize)
        })
        .collect();

    py.allow_threads(|| {
        runtime::get_runtime().block_on(async {
            let mut handles = Vec::with_capacity(op_addrs.len());

            for &op_addr in &op_addrs {
                let ctx = context_owned.clone();

                let handle = tokio::task::spawn_blocking(move || {
                    Python::with_gil(|py| {
                        // SAFETY: op and state are alive for the duration of block_on
                        let op = unsafe { &*(op_addr as *const OpConfig) };
                        let state = unsafe { &*(state_addr as *const EngineState) };

                        if let Err(e) = dispatch_op(py, op, state, &ctx) {
                            let error_msg = format!("{}", e);
                            state.set(
                                op.full_name.clone(),
                                "error".to_string(),
                                ctx.to_string(),
                                error_msg.to_object(py),
                            );
                            if let Ok(logging) = py.import_bound("logging") {
                                if let Ok(logger) =
                                    logging.call_method1("getLogger", ("hush.core",))
                                {
                                    let _ = logger.call_method1(
                                        "error",
                                        (format!(
                                            "[rush] Error in concurrent op {}: {}",
                                            op.full_name, error_msg
                                        ),),
                                    );
                                }
                            }
                        }
                    });
                });

                handles.push(handle);
            }

            for handle in handles {
                let _ = handle.await;
            }
        });
    });

    Ok(())
}

// =============================================================================
// Output collection
// =============================================================================

/// Collect graph outputs into a Python dict.
pub(crate) fn get_outputs(
    py: Python,
    config: &GraphConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<PyObject> {
    let result = PyDict::new_bound(py);
    for param in &config.outputs {
        if let Some(value) = base::resolve_param(py, param, state, context)? {
            result.set_item(&param.var_name, value.bind(py))?;
        } else if let Some(value) = state.get(py, &config.full_name, &param.var_name, context) {
            result.set_item(&param.var_name, value.bind(py))?;
        }
    }
    Ok(result.into())
}

// =============================================================================
// Successor activation
// =============================================================================

/// Activate successors after an op completes.
pub(crate) fn activate_successors(
    py: Python,
    op: &OpConfig,
    op_name: &str,
    config: &GraphConfig,
    state: &EngineState,
    context: &str,
    ready_count: &mut AHashMap<String, i32>,
    soft_satisfied: &mut AHashSet<String>,
    queue: &mut Vec<String>,
) -> PyResult<()> {
    let all_successors = config
        .compiled_adj
        .get(op_name)
        .cloned()
        .unwrap_or_default();

    // For branch ops, filter to only the selected target
    let successors = if op.op_type == "branch" {
        let target = state
            .get(py, &op.full_name, "target", context)
            .and_then(|v| v.extract::<String>(py).ok());

        match target {
            Some(ref t) if t == "__END__" => Default::default(),
            Some(ref t) => {
                all_successors
                    .iter()
                    .filter(|e| e.target == *t)
                    .cloned()
                    .collect()
            }
            None => Default::default(),
        }
    } else {
        all_successors
    };

    for entry in &successors {
        if entry.is_soft {
            if soft_satisfied.contains(&entry.target) {
                continue;
            }
            soft_satisfied.insert(entry.target.clone());
        }

        if let Some(count) = ready_count.get_mut(&entry.target) {
            *count -= 1;
            if *count == 0 {
                queue.push(entry.target.clone());
            }
        }
    }

    Ok(())
}

// =============================================================================
// Nested graph execution
// =============================================================================

/// Execute a nested GraphOp: resolve inputs, run inner scheduling loop, push outputs.
pub(crate) fn run_nested(
    py: Python,
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    let inner = op.inner_graph.as_ref().ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "Graph op '{}' missing inner_graph config",
            op.full_name
        ))
    })?;

    // 1. Resolve inputs from parent scope
    for param in &op.inputs {
        if let Some(value) = base::resolve_param(py, param, state, context)? {
            state.set(
                op.full_name.clone(),
                param.var_name.clone(),
                context.to_string(),
                value,
            );
        }
    }

    // 2. Run inner graph scheduling loop
    run_graph(py, inner, state, context)?;

    // 3. Collect inner graph outputs
    for param in &inner.outputs {
        if let Some(value) = base::resolve_param(py, param, state, context)? {
            state.set(
                op.full_name.clone(),
                param.var_name.clone(),
                context.to_string(),
                value,
            );
        } else if let Some(value) = state.get(py, &inner.full_name, &param.var_name, context) {
            let value = value.clone_ref(py);
            state.set(
                op.full_name.clone(),
                param.var_name.clone(),
                context.to_string(),
                value,
            );
        }
    }

    // 4. Push output refs
    base::push_output_refs(py, op, state, context)?;

    Ok(())
}
