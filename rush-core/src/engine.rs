//! Rush — standalone Rust execution engine for Hush workflows.
//!
//! Mirrors Python's `engine.py` (Hush class).
//! Loads a serialized config dict (from `GraphOp.serialize()`),
//! builds internal scheduling structures, and runs the graph entirely in Rust.
//!
//! Supports: flat graphs, branch ops (soft edges), nested GraphOps,
//! iteration ops (ForOp, WhileOp) with context-based isolation.
//! Uses rayon for multi-core parallel execution of independent ops.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::config::GraphConfig;
use crate::ops::graph::graph_op;
use crate::states::state::EngineState;

/// Standalone Rust execution engine for Hush workflows.
///
/// Usage from Python:
///   config = graph.serialize()
///   engine = Rush(config)
///   result = engine.run({"x": 5})
#[pyclass]
pub struct Rush {
    config: GraphConfig,
}

#[pymethods]
impl Rush {
    /// Create a new Rush engine from a serialized config dict.
    #[new]
    fn new(py: Python, config: &Bound<'_, PyDict>) -> PyResult<Self> {
        let config = GraphConfig::from_dict(py, config)?;
        Ok(Rush { config })
    }

    /// Run the graph synchronously with the given inputs.
    ///
    /// Args:
    ///     inputs: Dict of input values (e.g. {"x": 5}).
    ///     request_id: Optional request ID for tracing.
    ///     user_id: Optional user ID for tracing.
    ///     session_id: Optional session ID for tracing.
    ///
    /// Returns:
    ///     Dict of output values including `$state` metadata.
    #[pyo3(signature = (inputs, request_id=None, user_id=None, session_id=None))]
    fn run(
        &self,
        py: Python,
        inputs: &Bound<'_, PyDict>,
        request_id: Option<String>,
        user_id: Option<String>,
        session_id: Option<String>,
    ) -> PyResult<PyObject> {
        let state = EngineState::new();
        let context = "";

        // Store request_id on state (used by streaming ops for STREAM_SERVICE)
        if let Some(ref rid) = request_id {
            state.set_request_id(rid.clone());
        }

        // 1. Store inputs into state under the graph's full_name
        for (k, v) in inputs.iter() {
            let key: String = k.extract()?;
            state.set(
                self.config.full_name.clone(),
                key,
                context.to_string(),
                v.unbind(),
            );
        }

        // 2. Run the graph scheduling loop
        graph_op::run_graph(py, &self.config, &state, context)?;

        // 3. Collect graph outputs
        let result = graph_op::collect_outputs(py, &self.config, &state, context)?;

        // 4. Attach $state metadata (tags, execution_order, values, tracing IDs)
        //    Mirrors Python's result["$state"] = state
        if let Ok(dict) = result.downcast_bound::<PyDict>(py) {
            let state_meta = PyDict::new_bound(py);

            // Tags
            let tags_list = PyList::new_bound(py, state.tags());
            state_meta.set_item("tags", tags_list)?;

            // Execution order
            let exec_list = PyList::empty_bound(py);
            for (op_name, parent, ctx) in state.execution_order() {
                let entry = PyDict::new_bound(py);
                entry.set_item("op", &op_name)?;
                entry.set_item("parent", &parent)?;
                entry.set_item("context_id", &ctx)?;
                exec_list.append(entry)?;
            }
            state_meta.set_item("execution_order", exec_list)?;

            // Tracing metadata IDs
            if let Some(ref rid) = request_id {
                state_meta.set_item("request_id", rid)?;
            }
            if let Some(ref uid) = user_id {
                state_meta.set_item("user_id", uid)?;
            }
            if let Some(ref sid) = session_id {
                state_meta.set_item("session_id", sid)?;
            }

            // Per-op values: {"op_name": {"var_name": {"ctx": value}}}
            // Needed by TraceCollector to read I/O, timing, errors per op.
            let values_dict = PyDict::new_bound(py);
            for ((op, var, ctx), value) in state.values_snapshot(py) {
                // Get or create op dict
                let op_dict = match values_dict.get_item(&op)? {
                    Some(d) => d.downcast_into::<PyDict>().map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyTypeError, _>(format!(
                            "Expected dict for op '{}': {}",
                            op, e
                        ))
                    })?,
                    None => {
                        let d = PyDict::new_bound(py);
                        values_dict.set_item(&op, &d)?;
                        d
                    }
                };
                // Get or create var dict
                let var_dict = match op_dict.get_item(&var)? {
                    Some(d) => d.downcast_into::<PyDict>().map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyTypeError, _>(format!(
                            "Expected dict for var '{}': {}",
                            var, e
                        ))
                    })?,
                    None => {
                        let d = PyDict::new_bound(py);
                        op_dict.set_item(&var, &d)?;
                        d
                    }
                };
                var_dict.set_item(&ctx, value.bind(py))?;
            }
            state_meta.set_item("values", values_dict)?;

            dict.set_item("$state", state_meta)?;
        }

        Ok(result)
    }
}
