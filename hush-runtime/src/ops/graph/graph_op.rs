use ahash::{AHashMap, AHashSet};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};
use smallvec::SmallVec;

use crate::ops::transform::func_op;
use crate::states::state::RustMemoryState;

// =============================================================================
// Types (previously in scheduler/types.rs)
// =============================================================================

/// An edge in the compiled adjacency list.
#[derive(Clone, Debug)]
pub struct AdjEntry {
    pub target: String,
    pub is_soft: bool,
}

/// Mutable per-run state for scheduling.
/// Created fresh for each `graph.run()` call via `CompiledGraph::new_run_state()`.
#[pyclass]
pub struct RunState {
    /// How many predecessors each op still waits on. When it hits 0 the op is ready.
    pub ready_count: AHashMap<String, i32>,
    /// Ops whose soft-edge group has already been satisfied (counted once).
    pub soft_satisfied: AHashSet<String>,
}

#[pymethods]
impl RunState {
    fn __repr__(&self) -> String {
        format!(
            "RunState(ready={}, soft_satisfied={})",
            self.ready_count.len(),
            self.soft_satisfied.len()
        )
    }
}

/// A ready op returned to Python: (op_name, can_inline).
pub type ReadyOp = (String, bool);

/// Adjacency list type: each op has a small vec of successors.
pub type AdjList = AHashMap<String, SmallVec<[AdjEntry; 4]>>;

// =============================================================================
// OpMeta — pre-compiled per-op metadata for inline execution
// =============================================================================

/// Metadata for a single input parameter of an op.
struct InputMeta {
    /// Input variable name (e.g. "x")
    var_name: String,
    /// True if this input is a PARENT ref → use parent_context
    uses_parent_ctx: bool,
    /// Fallback literal value (non-Ref Param.value), or None
    literal_value: PyObject,
    /// Fallback default value (Param.default), or None
    default_value: PyObject,
}

/// Pre-compiled metadata for a single op, extracted at from_graph() time.
struct OpMeta {
    /// The op's full_name (e.g. "graph.double")
    full_name: String,
    /// Rust op name from @rust_op decorator (e.g. "rust_double"), or None
    rust_op_name: Option<String>,
    /// Input parameter metadata
    inputs: Vec<InputMeta>,
    /// Output variable names (e.g. ["result"])
    output_var_names: Vec<String>,
    /// True if rust_op_name is set AND the registry has this op
    is_rust_executable: bool,
    /// Parent op's full_name (for record_execution)
    parent_full_name: String,
}

// =============================================================================
// CompiledGraph (previously RustScheduler in scheduler/mod.rs)
// =============================================================================

/// Pre-compiled graph topology for O(1) scheduling decisions.
///
/// Built once from a Python `GraphOp` at `graph.build()` time.
/// At runtime, only primitive types cross the FFI boundary —
/// no Python objects touch the scheduling hot path.
#[pyclass]
pub struct CompiledGraph {
    /// op_name -> [(successor_name, is_soft), ...]
    adjacency: AdjList,
    /// op_name -> initial ready_count (how many preds to wait for)
    initial_ready_count: AHashMap<String, i32>,
    /// Entry ops (those with ready_count == 0)
    entries: Vec<String>,
    /// op_name -> true if the op can be inlined (sync leaf, no executor)
    can_inline: AHashMap<String, bool>,
    /// op_name -> true if the op is a branch op
    is_branch: AHashMap<String, bool>,
    /// short_name -> full_name for branch ops (used for state access)
    branch_full_names: AHashMap<String, String>,
    /// op_name -> pre-compiled metadata for inline execution (Phase 5)
    op_meta: AHashMap<String, OpMeta>,
}

#[pymethods]
impl CompiledGraph {
    /// Build a CompiledGraph from a compiled Python GraphOp.
    ///
    /// Reads: `_compiled_adj`, `initial_ready_count`, `entries`, `_ops`
    /// from the GraphOp instance. Called once at build time.
    #[staticmethod]
    fn from_graph(graph: &Bound<'_, PyAny>) -> PyResult<Self> {
        let compiled_adj_obj = graph.getattr("_compiled_adj")?;
        let ready_count_obj = graph.getattr("initial_ready_count")?;
        let entries_obj = graph.getattr("entries")?;
        let ops_obj = graph.getattr("_ops")?;

        // Build adjacency list
        let compiled_adj: &Bound<'_, PyDict> = compiled_adj_obj.downcast()?;
        let mut adjacency: AdjList = AHashMap::with_capacity(compiled_adj.len());

        for (key, value) in compiled_adj.iter() {
            let op_name: String = key.extract()?;
            let successors: &Bound<'_, PyList> = value.downcast()?;
            let mut adj: SmallVec<[AdjEntry; 4]> = SmallVec::new();

            for item in successors.iter() {
                let tuple: &Bound<'_, PyTuple> = item.downcast()?;
                let target: String = tuple.get_item(0)?.extract()?;
                let is_soft: bool = tuple.get_item(1)?.extract()?;
                adj.push(AdjEntry { target, is_soft });
            }
            adjacency.insert(op_name, adj);
        }

        // Build ready_count map
        let rc_dict: &Bound<'_, PyDict> = ready_count_obj.downcast()?;
        let mut initial_ready_count: AHashMap<String, i32> =
            AHashMap::with_capacity(rc_dict.len());

        for (key, value) in rc_dict.iter() {
            let op_name: String = key.extract()?;
            let count: i32 = value.extract()?;
            initial_ready_count.insert(op_name, count);
        }

        // Build entries list
        let entries_list: &Bound<'_, PyList> = entries_obj.downcast()?;
        let mut entries: Vec<String> = Vec::with_capacity(entries_list.len());
        for item in entries_list.iter() {
            entries.push(item.extract()?);
        }

        // Build can_inline, is_branch, and op_meta from _ops
        let py = graph.py();
        let ops_dict: &Bound<'_, PyDict> = ops_obj.downcast()?;
        let mut can_inline: AHashMap<String, bool> = AHashMap::with_capacity(ops_dict.len());
        let mut is_branch: AHashMap<String, bool> = AHashMap::with_capacity(ops_dict.len());
        let mut branch_full_names: AHashMap<String, String> = AHashMap::new();
        let mut op_meta: AHashMap<String, OpMeta> = AHashMap::with_capacity(ops_dict.len());

        // Python helpers for type checks
        let ref_cls = py.import_bound("hush.core.states.ref")?.getattr("Ref")?;
        let graph_ptr = graph.as_ptr(); // for PARENT ref detection via object identity

        for (key, value) in ops_dict.iter() {
            let op_name: String = key.extract()?;

            // Check if branch op
            let op_type: String = value.getattr("type")?.extract()?;
            if op_type == "branch" {
                is_branch.insert(op_name.clone(), true);
                let full_name: String = value.getattr("full_name")?.extract()?;
                branch_full_names.insert(op_name.clone(), full_name);
            }

            // Check if can inline: not a GraphOp, core is not async, no executor
            let is_graph = value
                .getattr("_ops")
                .map(|v| !v.is_none())
                .unwrap_or(false);

            if is_graph {
                can_inline.insert(op_name.clone(), false);
                // No OpMeta for GraphOps (they have their own scheduling)
                continue;
            }

            let core = value.getattr("core");
            let is_async = match &core {
                Ok(c) => {
                    let asyncio = py.import_bound("asyncio")?;
                    asyncio
                        .call_method1("iscoroutinefunction", (c,))?
                        .extract::<bool>()?
                }
                Err(_) => false,
            };

            let executor: Option<String> = value
                .getattr("executor")
                .ok()
                .and_then(|v| v.extract().ok());

            let inline = !is_async && executor.is_none();
            can_inline.insert(op_name.clone(), inline);

            // ---- Phase 5: Extract OpMeta ----

            // full_name
            let full_name: String = value.getattr("full_name")?.extract()?;

            // rust_op_name from core._rust_op_name (if exists)
            let rust_op_name: Option<String> = core
                .as_ref()
                .ok()
                .and_then(|c| c.getattr("_rust_op_name").ok())
                .and_then(|attr| {
                    if attr.is_none() {
                        None
                    } else {
                        attr.extract::<String>().ok()
                    }
                });

            // is_rust_executable
            let is_rust_executable = rust_op_name
                .as_ref()
                .map(|name| func_op::has_internal(name))
                .unwrap_or(false);

            // parent full_name
            let parent_full_name: String = value
                .getattr("father")
                .ok()
                .and_then(|f| {
                    if f.is_none() {
                        None
                    } else {
                        f.getattr("full_name").ok()
                    }
                })
                .and_then(|n| n.extract::<String>().ok())
                .unwrap_or_default();

            // Extract input metadata
            let mut inputs_meta: Vec<InputMeta> = Vec::new();
            if let Ok(inputs_obj) = value.getattr("inputs") {
                if let Ok(inputs_dict) = inputs_obj.downcast::<PyDict>() {
                    for (k, param) in inputs_dict.iter() {
                        let var_name: String = k.extract()?;
                        let param_value = param.getattr("value")?;

                        // Check if Ref
                        let is_ref = param_value.is_instance(&ref_cls)?;

                        let uses_parent_ctx = if is_ref {
                            // Compare raw_source object identity with graph
                            param_value
                                .getattr("raw_source")
                                .map(|src| src.as_ptr() == graph_ptr)
                                .unwrap_or(false)
                        } else {
                            false
                        };

                        let literal_value = if !is_ref && !param_value.is_none() {
                            param_value.clone().unbind()
                        } else {
                            py.None()
                        };

                        let default_value = param
                            .getattr("default")
                            .map(|d| d.unbind())
                            .unwrap_or_else(|_| py.None());

                        inputs_meta.push(InputMeta {
                            var_name,
                            uses_parent_ctx,
                            literal_value,
                            default_value,
                        });
                    }
                }
            }

            // Extract output variable names
            let mut output_var_names: Vec<String> = Vec::new();
            if let Ok(outputs_obj) = value.getattr("outputs") {
                if let Ok(outputs_dict) = outputs_obj.downcast::<PyDict>() {
                    for (k, _) in outputs_dict.iter() {
                        output_var_names.push(k.extract()?);
                    }
                }
            }

            op_meta.insert(
                op_name,
                OpMeta {
                    full_name,
                    rust_op_name,
                    inputs: inputs_meta,
                    output_var_names,
                    is_rust_executable,
                    parent_full_name,
                },
            );
        }

        Ok(Self {
            adjacency,
            initial_ready_count,
            entries,
            can_inline,
            is_branch,
            branch_full_names,
            op_meta,
        })
    }

    /// Build from a raw Python dict (for testing without a real GraphOp).
    ///
    /// Expected dict keys:
    ///   - adjacency: {str: [(str, bool), ...]}
    ///   - ready_count: {str: int}
    ///   - entries: [str]
    ///   - can_inline: {str: bool}
    ///   - is_branch: {str: bool}
    #[staticmethod]
    fn from_dict(data: &Bound<'_, PyDict>) -> PyResult<Self> {
        // adjacency
        let adj_obj = data
            .get_item("adjacency")?
            .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("missing 'adjacency'"))?;
        let adj_dict: &Bound<'_, PyDict> = adj_obj.downcast()?;
        let mut adjacency: AdjList = AHashMap::with_capacity(adj_dict.len());

        for (key, value) in adj_dict.iter() {
            let op_name: String = key.extract()?;
            let successors: &Bound<'_, PyList> = value.downcast()?;
            let mut adj: SmallVec<[AdjEntry; 4]> = SmallVec::new();

            for item in successors.iter() {
                let tuple: &Bound<'_, PyTuple> = item.downcast()?;
                let target: String = tuple.get_item(0)?.extract()?;
                let is_soft: bool = tuple.get_item(1)?.extract()?;
                adj.push(AdjEntry { target, is_soft });
            }
            adjacency.insert(op_name, adj);
        }

        // ready_count
        let rc_obj = data
            .get_item("ready_count")?
            .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("missing 'ready_count'"))?;
        let rc_dict: &Bound<'_, PyDict> = rc_obj.downcast()?;
        let mut initial_ready_count: AHashMap<String, i32> =
            AHashMap::with_capacity(rc_dict.len());
        for (key, value) in rc_dict.iter() {
            initial_ready_count.insert(key.extract()?, value.extract()?);
        }

        // entries
        let entries_obj = data
            .get_item("entries")?
            .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("missing 'entries'"))?;
        let entries_list: &Bound<'_, PyList> = entries_obj.downcast()?;
        let entries: Vec<String> = entries_list
            .iter()
            .map(|item| item.extract())
            .collect::<PyResult<_>>()?;

        // can_inline
        let ci_obj = data
            .get_item("can_inline")?
            .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("missing 'can_inline'"))?;
        let ci_dict: &Bound<'_, PyDict> = ci_obj.downcast()?;
        let mut can_inline: AHashMap<String, bool> = AHashMap::with_capacity(ci_dict.len());
        for (key, value) in ci_dict.iter() {
            can_inline.insert(key.extract()?, value.extract()?);
        }

        // is_branch
        let ib_obj = data
            .get_item("is_branch")?
            .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("missing 'is_branch'"))?;
        let ib_dict: &Bound<'_, PyDict> = ib_obj.downcast()?;
        let mut is_branch: AHashMap<String, bool> = AHashMap::with_capacity(ib_dict.len());
        for (key, value) in ib_dict.iter() {
            is_branch.insert(key.extract()?, value.extract()?);
        }

        // branch_full_names (optional — defaults to empty)
        let mut branch_full_names: AHashMap<String, String> = AHashMap::new();
        if let Some(bfn_obj) = data.get_item("branch_full_names")? {
            let bfn_dict: &Bound<'_, PyDict> = bfn_obj.downcast()?;
            for (key, value) in bfn_dict.iter() {
                branch_full_names.insert(key.extract()?, value.extract()?);
            }
        }

        Ok(Self {
            adjacency,
            initial_ready_count,
            entries,
            can_inline,
            is_branch,
            branch_full_names,
            op_meta: AHashMap::new(), // from_dict doesn't provide op_meta
        })
    }

    /// Create a fresh per-run scheduling state.
    fn new_run_state(&self) -> RunState {
        RunState {
            ready_count: self.initial_ready_count.clone(),
            soft_satisfied: AHashSet::new(),
        }
    }

    /// Get entry ops with their can_inline flag.
    /// Returns: [(op_name, can_inline), ...]
    fn get_entries(&self) -> Vec<ReadyOp> {
        self.entries
            .iter()
            .map(|name| {
                let inline = self.can_inline.get(name).copied().unwrap_or(false);
                (name.clone(), inline)
            })
            .collect()
    }

    /// Core scheduling: given a completed op, return newly-ready ops.
    ///
    /// For branch ops, `branch_target` specifies which successor was chosen
    /// (resolved by Python). For non-branch ops, pass None.
    ///
    /// Returns: [(op_name, can_inline), ...]
    #[pyo3(signature = (completed_op, state, branch_target=None))]
    fn activate_successors(
        &self,
        completed_op: &str,
        state: &mut RunState,
        branch_target: Option<&str>,
    ) -> Vec<ReadyOp> {
        let successors = if self.is_branch.get(completed_op).copied().unwrap_or(false) {
            // Branch op: only activate the chosen target
            match branch_target {
                Some(target) => {
                    // Find the matching edge to preserve soft flag
                    if let Some(adj) = self.adjacency.get(completed_op) {
                        let mut result = SmallVec::<[AdjEntry; 4]>::new();
                        for entry in adj.iter() {
                            if entry.target == target {
                                result.push(entry.clone());
                                break;
                            }
                        }
                        result
                    } else {
                        SmallVec::new()
                    }
                }
                None => SmallVec::new(),
            }
        } else {
            // Non-branch: activate all successors
            self.adjacency
                .get(completed_op)
                .cloned()
                .unwrap_or_default()
        };

        let mut newly_ready = Vec::new();

        for entry in &successors {
            if entry.is_soft {
                if state.soft_satisfied.contains(&entry.target) {
                    continue;
                }
                state.soft_satisfied.insert(entry.target.clone());
            }

            if let Some(count) = state.ready_count.get_mut(&entry.target) {
                *count -= 1;
                if *count == 0 {
                    let inline = self.can_inline.get(&entry.target).copied().unwrap_or(false);
                    newly_ready.push((entry.target.clone(), inline));
                }
            }
        }

        newly_ready
    }

    /// Combined branch resolution + successor activation in a single Rust call.
    ///
    /// For branch ops, reads the branch target directly from RustMemoryState
    /// (eliminating the Python `op.get_target(state, ctx)` callback).
    /// For non-branch ops, behaves identically to `activate_successors(op, state, None)`.
    ///
    /// Returns: (newly_ready_ops, is_end)
    ///   - newly_ready_ops: [(op_name, can_inline), ...]
    ///   - is_end: true if branch target was "__END__" (graph should stop this path)
    #[pyo3(signature = (completed_op, run_state, memory_state, context_id=None))]
    fn activate_successors_with_state(
        &self,
        py: Python,
        completed_op: &str,
        run_state: &mut RunState,
        memory_state: &RustMemoryState,
        context_id: Option<&str>,
    ) -> (Vec<ReadyOp>, bool) {
        let is_branch = self.is_branch.get(completed_op).copied().unwrap_or(false);

        if !is_branch {
            // Non-branch: activate all successors
            let ready = self.activate_successors(completed_op, run_state, None);
            return (ready, false);
        }

        // Branch op: read target directly from Rust state
        let ctx = context_id.unwrap_or("main");
        let branch_target = self
            .branch_full_names
            .get(completed_op)
            .and_then(|full_name| memory_state.get_string(py, full_name, "target", ctx));

        match branch_target {
            Some(ref target) if target == "__END__" => (Vec::new(), true),
            Some(ref target) => {
                let ready = self.activate_successors(completed_op, run_state, Some(target));
                (ready, false)
            }
            None => (Vec::new(), false),
        }
    }

    /// Check if an op is a branch op.
    fn is_branch_op(&self, op_name: &str) -> bool {
        self.is_branch.get(op_name).copied().unwrap_or(false)
    }

    /// Get the number of ops in the graph.
    fn num_ops(&self) -> usize {
        self.initial_ready_count.len()
    }

    // =========================================================================
    // Phase 5: Inline execution
    // =========================================================================

    /// Try to execute an op entirely in Rust (get_inputs → execute → store_result).
    ///
    /// Returns true if the op was executed successfully in Rust.
    /// Returns false if the op is not Rust-executable (caller should fall back to Python).
    /// Raises PyErr if the Rust op itself fails.
    #[pyo3(signature = (op_name, memory_state, context_id=None, parent_context=None))]
    fn try_execute_inline(
        &self,
        py: Python,
        op_name: &str,
        memory_state: &mut RustMemoryState,
        context_id: Option<&str>,
        parent_context: Option<&str>,
    ) -> PyResult<bool> {
        let meta = match self.op_meta.get(op_name) {
            Some(m) => m,
            None => return Ok(false),
        };

        if !meta.is_rust_executable {
            return Ok(false);
        }

        let rust_op_name = match &meta.rust_op_name {
            Some(name) => name.as_str(),
            None => return Ok(false),
        };

        let ctx = context_id.unwrap_or("main");
        let parent_ctx = parent_context.unwrap_or(ctx);

        // 1. Record execution
        memory_state.record_execution_internal(&meta.full_name, &meta.parent_full_name, ctx);

        // 2. Build inputs dict
        let inputs_dict = PyDict::new_bound(py);
        for input_meta in &meta.inputs {
            let lookup_ctx = if input_meta.uses_parent_ctx {
                parent_ctx
            } else {
                ctx
            };

            let value = memory_state.get_value(py, &meta.full_name, &input_meta.var_name, lookup_ctx);

            if !value.is_none(py) {
                inputs_dict.set_item(&input_meta.var_name, &value)?;
            } else if !input_meta.literal_value.is_none(py) {
                inputs_dict.set_item(&input_meta.var_name, &input_meta.literal_value)?;
            } else if !input_meta.default_value.is_none(py) {
                inputs_dict.set_item(&input_meta.var_name, &input_meta.default_value)?;
            }
        }

        // 3. Execute via Rust registry
        let result = func_op::execute_internal(py, rust_op_name, &inputs_dict)?;
        let result_dict = match result {
            Some(obj) => obj,
            None => return Ok(false), // op not found in registry
        };

        // 4. Store outputs
        let result_bound = result_dict.bind(py);
        if let Ok(dict) = result_bound.downcast::<PyDict>() {
            for (k, v) in dict.iter() {
                let key: String = k.extract()?;
                // Skip $tags
                if key == "$tags" {
                    if let Ok(tags) = v.extract::<Vec<String>>() {
                        for tag in tags {
                            memory_state.set_value(
                                py,
                                &meta.full_name,
                                "__tag__",
                                ctx,
                                py.None(),
                            ).ok(); // best-effort
                            let _ = tag; // tags handled by Python side
                        }
                    }
                    continue;
                }
                memory_state.set_value(py, &meta.full_name, &key, ctx, v.unbind())?;
            }
        }

        Ok(true)
    }

    /// Check if all ops in this graph can be executed entirely in Rust.
    ///
    /// Returns true only if every op has OpMeta with is_rust_executable=true
    /// AND can_inline=true (no async ops, no nested GraphOps).
    fn all_rust_executable(&self) -> bool {
        if self.op_meta.is_empty() {
            return false;
        }
        // All ops in the graph must have op_meta AND be Rust-executable AND inlineable
        for (name, _) in &self.initial_ready_count {
            let can_inline = self.can_inline.get(name).copied().unwrap_or(false);
            if !can_inline {
                return false;
            }
            match self.op_meta.get(name) {
                Some(meta) => {
                    if !meta.is_rust_executable {
                        return false;
                    }
                }
                None => return false, // GraphOp or missing metadata
            }
        }
        true
    }

    /// Run the entire graph synchronously in Rust.
    ///
    /// Only valid when `all_rust_executable()` returns true.
    /// Replicates the scheduling loop from `_run_rust_scheduled()` entirely in Rust.
    #[pyo3(signature = (memory_state, context_id=None, parent_context=None))]
    fn run_sync(
        &self,
        py: Python,
        memory_state: &mut RustMemoryState,
        context_id: Option<&str>,
        parent_context: Option<&str>,
    ) -> PyResult<()> {
        let mut run_state = self.new_run_state();
        let ctx = context_id.unwrap_or("main");
        let parent_ctx = parent_context.unwrap_or(ctx);

        // Process entries
        let mut queue: Vec<String> = self.entries.clone();

        while let Some(op_name) = queue.first().cloned() {
            queue.remove(0);

            // Execute inline
            self.try_execute_inline(
                py,
                &op_name,
                memory_state,
                Some(ctx),
                Some(parent_ctx),
            )?;

            // Activate successors (with branch resolution)
            let (newly_ready, is_end) = self.activate_successors_with_state(
                py,
                &op_name,
                &mut run_state,
                memory_state,
                context_id,
            );

            if is_end {
                continue;
            }

            for (name, _can_inline) in newly_ready {
                queue.push(name);
            }
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_compiled_graph(
        adj: Vec<(&str, Vec<(&str, bool)>)>,
        ready_count: Vec<(&str, i32)>,
        entries: Vec<&str>,
        can_inline: Vec<(&str, bool)>,
        is_branch: Vec<(&str, bool)>,
    ) -> CompiledGraph {
        let mut adjacency: AdjList = AHashMap::new();
        for (op, succs) in adj {
            let mut sv = SmallVec::new();
            for (target, soft) in succs {
                sv.push(AdjEntry {
                    target: target.to_string(),
                    is_soft: soft,
                });
            }
            adjacency.insert(op.to_string(), sv);
        }

        CompiledGraph {
            adjacency,
            initial_ready_count: ready_count
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .collect(),
            entries: entries.into_iter().map(|s| s.to_string()).collect(),
            can_inline: can_inline
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .collect(),
            is_branch: is_branch
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .collect(),
            branch_full_names: AHashMap::new(),
            op_meta: AHashMap::new(),
        }
    }

    #[test]
    fn test_linear_chain() {
        let cg = make_compiled_graph(
            vec![
                ("A", vec![("B", false)]),
                ("B", vec![("C", false)]),
                ("C", vec![]),
            ],
            vec![("A", 0), ("B", 1), ("C", 1)],
            vec!["A"],
            vec![("A", true), ("B", true), ("C", true)],
            vec![],
        );

        let mut state = cg.new_run_state();
        let entries = cg.get_entries();
        assert_eq!(entries, vec![("A".to_string(), true)]);

        let ready = cg.activate_successors("A", &mut state, None);
        assert_eq!(ready, vec![("B".to_string(), true)]);

        let ready = cg.activate_successors("B", &mut state, None);
        assert_eq!(ready, vec![("C".to_string(), true)]);

        let ready = cg.activate_successors("C", &mut state, None);
        assert!(ready.is_empty());
    }

    #[test]
    fn test_parallel_fork_join() {
        let cg = make_compiled_graph(
            vec![
                ("A", vec![("C", false)]),
                ("B", vec![("C", false)]),
                ("C", vec![]),
            ],
            vec![("A", 0), ("B", 0), ("C", 2)],
            vec!["A", "B"],
            vec![("A", true), ("B", true), ("C", true)],
            vec![],
        );

        let mut state = cg.new_run_state();

        let ready = cg.activate_successors("A", &mut state, None);
        assert!(ready.is_empty());

        let ready = cg.activate_successors("B", &mut state, None);
        assert_eq!(ready, vec![("C".to_string(), true)]);
    }

    #[test]
    fn test_soft_edge_branch() {
        let cg = make_compiled_graph(
            vec![
                ("branch", vec![("case_a", true), ("case_b", true)]),
                ("case_a", vec![("merge", true)]),
                ("case_b", vec![("merge", true)]),
                ("merge", vec![]),
            ],
            vec![
                ("branch", 0),
                ("case_a", 1),
                ("case_b", 1),
                ("merge", 1),
            ],
            vec!["branch"],
            vec![
                ("branch", true),
                ("case_a", true),
                ("case_b", true),
                ("merge", true),
            ],
            vec![("branch", true)],
        );

        let mut state = cg.new_run_state();

        let ready = cg.activate_successors("branch", &mut state, Some("case_a"));
        assert_eq!(ready, vec![("case_a".to_string(), true)]);

        let ready = cg.activate_successors("case_a", &mut state, None);
        assert_eq!(ready, vec![("merge".to_string(), true)]);
    }

    #[test]
    fn test_soft_edge_only_counts_once() {
        let cg = make_compiled_graph(
            vec![
                ("case_a", vec![("merge", true)]),
                ("case_b", vec![("merge", true)]),
                ("merge", vec![]),
            ],
            vec![("case_a", 0), ("case_b", 0), ("merge", 1)],
            vec!["case_a", "case_b"],
            vec![("case_a", true), ("case_b", true), ("merge", true)],
            vec![],
        );

        let mut state = cg.new_run_state();

        let ready = cg.activate_successors("case_a", &mut state, None);
        assert_eq!(ready, vec![("merge".to_string(), true)]);

        let ready = cg.activate_successors("case_b", &mut state, None);
        assert!(ready.is_empty());
    }

    #[test]
    fn test_diamond_graph() {
        let cg = make_compiled_graph(
            vec![
                ("A", vec![("B", false), ("C", false)]),
                ("B", vec![("D", false)]),
                ("C", vec![("D", false)]),
                ("D", vec![]),
            ],
            vec![("A", 0), ("B", 1), ("C", 1), ("D", 2)],
            vec!["A"],
            vec![
                ("A", true),
                ("B", true),
                ("C", true),
                ("D", true),
            ],
            vec![],
        );

        let mut state = cg.new_run_state();

        let ready = cg.activate_successors("A", &mut state, None);
        assert_eq!(ready.len(), 2);
        let names: Vec<&str> = ready.iter().map(|(n, _)| n.as_str()).collect();
        assert!(names.contains(&"B"));
        assert!(names.contains(&"C"));

        let ready = cg.activate_successors("B", &mut state, None);
        assert!(ready.is_empty());

        let ready = cg.activate_successors("C", &mut state, None);
        assert_eq!(ready, vec![("D".to_string(), true)]);
    }

    #[test]
    fn test_new_run_state_is_independent() {
        let cg = make_compiled_graph(
            vec![("A", vec![("B", false)]), ("B", vec![])],
            vec![("A", 0), ("B", 1)],
            vec!["A"],
            vec![("A", true), ("B", true)],
            vec![],
        );

        let mut state1 = cg.new_run_state();
        let mut state2 = cg.new_run_state();

        let ready1 = cg.activate_successors("A", &mut state1, None);
        assert_eq!(ready1, vec![("B".to_string(), true)]);

        assert_eq!(*state2.ready_count.get("B").unwrap(), 1);

        let ready2 = cg.activate_successors("A", &mut state2, None);
        assert_eq!(ready2, vec![("B".to_string(), true)]);
    }
}
