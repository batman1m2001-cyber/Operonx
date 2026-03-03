//! Graph op execution — scheduling loop, output collection, nested graphs.
//!
//! Mirrors Python's `ops/graph/graph_op.py` (GraphOp).
//! Pure Rust on serde_json::Value — no Python/GIL needed.
//! Supports batch concurrent execution via tokio when multiple independent ops are ready.

use std::sync::Arc;

use ahash::{AHashMap, AHashSet};
use serde_json::Value;

use crate::config::{GraphConfig, OpBound, OpConfig};
use crate::error::RushError;
use crate::ops::base;
use crate::ops::iteration::{for_op, map_op, while_op};
use crate::runtime;
use crate::states::state::EngineState;

// =============================================================================
// Op dispatch
// =============================================================================

/// Dispatch an op to the appropriate runner based on op_type.
pub(crate) fn dispatch_op(
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> Result<(), RushError> {
    match op.op_type.as_str() {
        "graph" => run_nested(op, state, context),
        "for" => for_op::run(op, state, context),
        "while" => while_op::run(op, state, context),
        "map" => map_op::run(op, state, context),
        "stream" => Err(RushError::UnsupportedOp(
            "AIterOp (stream) is not supported in rust mode v1. Use MapOp instead.".into(),
        )),
        "branch" => base::execute_branch(op, state, context),
        _ => base::run(op, state, context),
    }
}

// =============================================================================
// Graph scheduling loop
// =============================================================================

/// Run a graph's scheduling loop (used for both top-level and nested graphs).
///
/// Supports two execution modes:
/// - **Sequential** (default): ops executed one at a time
/// - **Concurrent** (batch): independent ops executed via tokio's thread pool
///
/// Concurrent mode activates when a batch has 2+ ops AND at least one has
/// `bound = Io` or a Rust-native op.
pub(crate) fn run_graph(
    config: &GraphConfig,
    state: &EngineState,
    context: &str,
) -> Result<(), RushError> {
    let mut ready_count = config.initial_ready_count.clone();
    let mut soft_satisfied: AHashSet<String> = AHashSet::new();
    let mut queue: Vec<String> = config.entries.clone();

    while !queue.is_empty() {
        let batch: Vec<String> = queue.drain(..).collect();

        // Check if concurrent execution would benefit this batch.
        let use_concurrent = batch.len() > 1
            && batch.iter().any(|name| {
                config.ops.get(name).map_or(false, |op| {
                    op.bound == OpBound::Io || op.rust_op.is_some() || op.op_type == "graph"
                })
            });

        if use_concurrent {
            run_batch(&batch, config, state, context)?;

            for op_name in &batch {
                let op = config.ops.get(op_name).ok_or_else(|| {
                    RushError::ConfigError(format!("Op '{}' not found in config", op_name))
                })?;
                activate_successors(
                    op,
                    op_name,
                    config,
                    state,
                    context,
                    &mut ready_count,
                    &mut soft_satisfied,
                    &mut queue,
                )?;
            }
        } else {
            for op_name in batch {
                let op = config.ops.get(&op_name).ok_or_else(|| {
                    RushError::ConfigError(format!("Op '{}' not found in config", op_name))
                })?;

                // Pure Rust — just dispatch directly, no GIL dance
                dispatch_op(op, state, context)?;

                activate_successors(
                    op,
                    &op_name,
                    config,
                    state,
                    context,
                    &mut ready_count,
                    &mut soft_satisfied,
                    &mut queue,
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
/// Each op is spawned as a `tokio::task::spawn_blocking` task.
/// No GIL needed — pure Rust execution.
fn run_batch(
    batch: &[String],
    config: &GraphConfig,
    state: &EngineState,
    context: &str,
) -> Result<(), RushError> {
    let context_owned = context.to_string();

    // SAFETY: config and state are alive for the duration of block_on.
    // We use raw pointer casts because spawn_blocking requires 'static.
    let state_addr = state as *const EngineState as usize;

    let op_addrs: Vec<(String, usize)> = batch
        .iter()
        .filter_map(|name| {
            config
                .ops
                .get(name)
                .map(|op| (name.clone(), op as *const OpConfig as usize))
        })
        .collect();

    runtime::get_runtime().block_on(async {
        let mut handles = Vec::with_capacity(op_addrs.len());

        for (op_name, op_addr) in &op_addrs {
            let ctx = context_owned.clone();
            let name = op_name.clone();
            let op_a = *op_addr;

            let handle = tokio::task::spawn_blocking(move || {
                // SAFETY: op and state are alive for the duration of block_on
                let op = unsafe { &*(op_a as *const OpConfig) };
                let state = unsafe { &*(state_addr as *const EngineState) };

                if let Err(e) = dispatch_op(op, state, &ctx) {
                    state.set(
                        &name,
                        "error",
                        &ctx,
                        Value::String(format!("{}", e)),
                    );
                    log::error!("[rush] Error in concurrent op {}: {}", name, e);
                }
            });

            handles.push(handle);
        }

        for handle in handles {
            let _ = handle.await;
        }
    });

    Ok(())
}

// =============================================================================
// Output collection
// =============================================================================

/// Collect graph outputs as a serde_json::Value (JSON object).
///
/// When explicit `outputs` are defined, resolves each param via refs or graph state.
/// When `outputs` is empty, auto-forwards all values from terminal ops (those
/// whose adjacency points to `__end__`), matching Python's `>> END` behavior.
pub(crate) fn get_outputs(
    config: &GraphConfig,
    state: &EngineState,
    context: &str,
) -> Result<Value, RushError> {
    let mut result = serde_json::Map::new();

    if !config.outputs.is_empty() {
        // Explicit output mappings
        for param in &config.outputs {
            if let Some(value) = base::resolve_param(param, state, context)? {
                result.insert(param.var_name.clone(), value);
            } else if let Some(arc_val) = state.get(&config.full_name, &param.var_name, context) {
                let value = Arc::try_unwrap(arc_val).unwrap_or_else(|arc| (*arc).clone());
                result.insert(param.var_name.clone(), value);
            }
        }
    } else {
        // Auto-forward: collect all values from terminal ops (ops → __end__)
        for (op_name, adj_list) in &config.compiled_adj {
            let is_terminal = adj_list.iter().any(|e| e.target == "__end__");
            if is_terminal {
                if let Some(op) = config.ops.get(op_name) {
                    let prefix = &op.full_name;
                    for (key, value) in state.get_all_with_prefix(prefix, context) {
                        if !key.starts_with('$') {
                            result.insert(key, value);
                        }
                    }
                }
            }
        }
    }

    Ok(Value::Object(result))
}

// =============================================================================
// Successor activation
// =============================================================================

/// Activate successors after an op completes.
pub(crate) fn activate_successors(
    op: &OpConfig,
    op_name: &str,
    config: &GraphConfig,
    state: &EngineState,
    context: &str,
    ready_count: &mut AHashMap<String, i32>,
    soft_satisfied: &mut AHashSet<String>,
    queue: &mut Vec<String>,
) -> Result<(), RushError> {
    let all_successors = config
        .compiled_adj
        .get(op_name)
        .cloned()
        .unwrap_or_default();

    // For branch ops, filter to only the selected target
    let successors = if op.op_type == "branch" {
        let target = state
            .get(&op.full_name, "target", context)
            .and_then(|v| v.as_str().map(|s| s.to_string()));

        match target {
            Some(ref t) if t == "__END__" => Default::default(),
            Some(ref t) => all_successors
                .iter()
                .filter(|e| e.target == *t)
                .cloned()
                .collect(),
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
    op: &OpConfig,
    state: &EngineState,
    context: &str,
) -> Result<(), RushError> {
    let inner = op.inner_graph.as_ref().ok_or_else(|| {
        RushError::ConfigError(format!(
            "Graph op '{}' missing inner_graph config",
            op.full_name
        ))
    })?;

    // 1. Resolve inputs from parent scope
    for param in &op.inputs {
        if let Some(value) = base::resolve_param(param, state, context)? {
            state.set(&op.full_name, &param.var_name, context, value);
        }
    }

    // 2. Run inner graph scheduling loop
    run_graph(inner, state, context)?;

    // 3. Collect inner graph outputs
    if !inner.outputs.is_empty() {
        // Explicit output mappings
        for param in &inner.outputs {
            if let Some(value) = base::resolve_param(param, state, context)? {
                state.set(&op.full_name, &param.var_name, context, value);
            } else if let Some(arc_val) =
                state.get(&inner.full_name, &param.var_name, context)
            {
                let value = Arc::try_unwrap(arc_val).unwrap_or_else(|arc| (*arc).clone());
                state.set(&op.full_name, &param.var_name, context, value);
            }
        }
    } else {
        // Auto-forward: collect all values from terminal ops (>> END),
        // matching Python's auto-forwarding behavior for nested graphs.
        for (inner_op_name, adj_list) in &inner.compiled_adj {
            let is_terminal = adj_list.iter().any(|e| e.target == "__end__");
            if is_terminal {
                if let Some(inner_op) = inner.ops.get(inner_op_name) {
                    for (key, value) in
                        state.get_all_with_prefix(&inner_op.full_name, context)
                    {
                        if !key.starts_with('$') {
                            state.set(&op.full_name, &key, context, value);
                        }
                    }
                }
            }
        }
    }

    // 4. Push output refs
    base::push_output_refs(op, state, context)?;

    Ok(())
}
