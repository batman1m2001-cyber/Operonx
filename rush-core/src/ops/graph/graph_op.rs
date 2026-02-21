//! Graph op execution — scheduling loop, output collection, nested graphs.
//!
//! Mirrors Python's `ops/graph/graph_op.py` (GraphOp).
//! Supports batch parallel execution via rayon when multiple independent ops are ready.

use ahash::{AHashMap, AHashSet};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use rayon::prelude::*;

use crate::config::{GraphConfig, OpConfig};
use crate::ops::base;
use crate::ops::iteration::{for_op, while_op};
use crate::states::state::EngineState;

// =============================================================================
// Graph scheduling loop
// =============================================================================

/// Run a graph's scheduling loop (used for both top-level and nested graphs).
///
/// Supports two execution modes:
/// - **Sequential** (default): ops executed one at a time (optimal for GIL-bound Python ops)
/// - **Parallel** (batch): independent ops executed via rayon thread pool
///   (benefits I/O-bound ops that release GIL, or Rust-native ops)
///
/// Parallel mode activates when multiple ops are ready AND at least one has a
/// Rust implementation (`rust_op`). Pure Python batches run sequentially to
/// avoid `allow_threads`/`with_gil` overhead when the GIL can't be released.
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
        // Drain all currently ready ops into a batch
        let batch: Vec<String> = queue.drain(..).collect();

        // Check if parallel execution would benefit this batch:
        // - Need 2+ ops (otherwise no parallelism)
        // - At least one op with rust_op (can release GIL for true parallelism)
        let use_parallel = batch.len() > 1 && batch.iter().any(|name| {
            config.ops.get(name).map_or(false, |op| op.rust_op.is_some())
        });

        if use_parallel {
            // Parallel execution via rayon (beneficial when GIL can be released)
            execute_batch_parallel(py, &batch, config, state, context)?;

            // Activate successors sequentially after all ops complete
            for op_name in &batch {
                let op = config.ops.get(op_name).ok_or_else(|| {
                    PyErr::new::<pyo3::exceptions::PyKeyError, _>(format!(
                        "Op '{}' not found in config",
                        op_name
                    ))
                })?;
                activate_successors(
                    py,
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
            // Sequential execution — one at a time with immediate successor activation
            for op_name in batch {
                let op = config.ops.get(&op_name).ok_or_else(|| {
                    PyErr::new::<pyo3::exceptions::PyKeyError, _>(format!(
                        "Op '{}' not found in config",
                        op_name
                    ))
                })?;

                match op.op_type.as_str() {
                    "graph" => execute_nested_graph(py, op, state, context)?,
                    "for" => for_op::execute_for_op(py, op, state, context)?,
                    "while" => while_op::execute_while_op(py, op, state, context)?,
                    _ => base::execute_leaf_op(py, op, state, context)?,
                }

                activate_successors(
                    py,
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
// Batch parallel execution
// =============================================================================

/// Execute a batch of independent ops in parallel using rayon.
///
/// All ops in the batch have ready_count == 0 (no dependencies on each other).
/// Each op runs in its own rayon thread, acquiring the GIL independently.
/// DashMap-based EngineState allows concurrent reads/writes safely.
fn execute_batch_parallel(
    py: Python,
    batch: &[String],
    config: &GraphConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<()> {
    // Release the GIL so rayon threads can acquire it independently
    py.allow_threads(|| {
        batch.par_iter().for_each(|op_name| {
            Python::with_gil(|py| {
                let op = match config.ops.get(op_name) {
                    Some(op) => op,
                    None => return, // Skip if not found (shouldn't happen)
                };

                let result = match op.op_type.as_str() {
                    "graph" => execute_nested_graph(py, op, state, context),
                    "for" => for_op::execute_for_op(py, op, state, context),
                    "while" => while_op::execute_while_op(py, op, state, context),
                    _ => base::execute_leaf_op(py, op, state, context),
                };

                // Handle errors from non-leaf ops (leaf ops catch internally)
                if let Err(e) = result {
                    let error_msg = format!("{}", e);
                    state.set(
                        op.full_name.clone(),
                        "error".to_string(),
                        context.to_string(),
                        error_msg.to_object(py),
                    );
                    if let Ok(logging) = py.import_bound("logging") {
                        if let Ok(logger) = logging.call_method1("getLogger", ("hush.core",)) {
                            let _ = logger.call_method1(
                                "error",
                                (format!(
                                    "[rush] Error in parallel op {}: {}",
                                    op.full_name, error_msg
                                ),),
                            );
                        }
                    }
                }
            });
        });
    });

    Ok(())
}

// =============================================================================
// Output collection
// =============================================================================

/// Collect graph outputs into a Python dict.
pub(crate) fn collect_outputs(
    py: Python,
    config: &GraphConfig,
    state: &EngineState,
    context: &str,
) -> PyResult<PyObject> {
    let result = PyDict::new_bound(py);
    for param in &config.outputs {
        // First try resolving via ref/literal/default
        if let Some(value) = base::resolve_param(py, param, state, context)? {
            result.set_item(&param.var_name, value.bind(py))?;
        } else if let Some(value) = state.get(py, &config.full_name, &param.var_name, context) {
            // Fall back to reading directly from graph state
            result.set_item(&param.var_name, value.bind(py))?;
        }
    }
    Ok(result.into())
}

// =============================================================================
// Successor activation
// =============================================================================

/// Activate successors after an op completes.
/// Handles branch target routing and soft edge deduplication.
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
            Some(ref t) if t == "__END__" => {
                // Branch to END — no successors to activate
                Default::default()
            }
            Some(ref t) => {
                // Filter to only the matching target successor
                all_successors
                    .iter()
                    .filter(|e| e.target == *t)
                    .cloned()
                    .collect()
            }
            None => {
                // No target resolved — skip all successors
                Default::default()
            }
        }
    } else {
        all_successors
    };

    // Activate each successor, handling soft edges
    for entry in &successors {
        if entry.is_soft {
            // Soft edge: only activate once per target
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
pub(crate) fn execute_nested_graph(
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

    // 1. Resolve inputs from parent scope → write to nested graph's state namespace
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

    // 2. Run the inner graph scheduling loop (reuses same EngineState —
    //    full_names are unique across nesting levels, e.g. "main.nested.step")
    run_graph(py, inner, state, context)?;

    // 3. Collect inner graph outputs into the nested graph op's state namespace
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

    // 4. Process output refs (push nested graph outputs to parent namespace)
    base::push_output_refs(py, op, state, context)?;

    Ok(())
}
