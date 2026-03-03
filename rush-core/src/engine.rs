//! Rush — standalone Rust execution engine for Hush workflows.
//!
//! Mirrors Python's `engine.py` (Hush class).
//! Takes a JSON config string (from `json.dumps(GraphOp.serialize())`),
//! parses into Rust config structs, and runs the graph entirely GIL-free.
//!
//! GIL is held only at the boundary: converting Python inputs → JSON and
//! JSON result → Python dict. The entire graph execution happens in
//! `py.allow_threads()` — true parallel execution for concurrent workflows.

use pyo3::prelude::*;
use pyo3::types::PyDict;

use serde_json::Value;

use crate::config::GraphConfig;
use crate::error::RushError;
use crate::ops::graph::graph_op;
use crate::states::state::EngineState;

/// Standalone Rust execution engine for Hush workflows.
///
/// Usage from Python:
///   config = graph.serialize()
///   config_json = json.dumps(config, default=str)
///   engine = Rush(config_json)
///   result = engine.run({"x": 5})
#[pyclass]
pub struct Rush {
    config: GraphConfig,
}

#[pymethods]
impl Rush {
    /// Create a new Rush engine from a JSON config string.
    #[new]
    fn new(config_json: &str) -> PyResult<Self> {
        let config = GraphConfig::from_json(config_json).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{e}"))
        })?;
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
        // 1. Convert Python inputs → serde_json (GIL held, fast)
        let json_inputs = rush_providers::py_serde::pydict_to_json(py, inputs).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "Failed to convert inputs: {e}"
            ))
        })?;

        // 2. Run ENTIRE graph GIL-free
        let json_result = py
            .allow_threads(|| {
                self.run_graph_json(json_inputs, request_id, user_id, session_id)
            })
            .map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("{e}"))
            })?;

        // 3. Convert result → Python dict (GIL held, fast)
        let py_result =
            rush_providers::py_serde::json_to_pydict(py, &json_result).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                    "Failed to convert result: {e}"
                ))
            })?;

        Ok(py_result)
    }
}

impl Rush {
    /// Pure Rust graph execution — no Python, no GIL.
    fn run_graph_json(
        &self,
        inputs: Value,
        request_id: Option<String>,
        user_id: Option<String>,
        session_id: Option<String>,
    ) -> Result<Value, RushError> {
        let state = EngineState::new();
        let context = "";

        if let Some(ref rid) = request_id {
            state.set_request_id(rid.clone());
        }

        // 1. Store inputs under graph's full_name (consume map, no cloning)
        if let Value::Object(map) = inputs {
            for (key, value) in map {
                state.set(&self.config.full_name, &key, context, value);
            }
        }

        // 2. Run graph — entirely GIL-free
        graph_op::run_graph(&self.config, &state, context)?;

        // 3. Collect outputs
        let mut result = graph_op::get_outputs(&self.config, &state, context)?;

        // 4. Build $state metadata
        let mut state_meta = serde_json::Map::new();
        state_meta.insert(
            "tags".into(),
            Value::Array(state.tags().into_iter().map(Value::String).collect()),
        );
        if let Some(ref rid) = request_id {
            state_meta.insert("request_id".into(), Value::String(rid.clone()));
        }
        if let Some(ref uid) = user_id {
            state_meta.insert("user_id".into(), Value::String(uid.clone()));
        }
        if let Some(ref sid) = session_id {
            state_meta.insert("session_id".into(), Value::String(sid.clone()));
        }
        state_meta.insert("values".into(), state.values_snapshot());

        if let Value::Object(ref mut map) = result {
            map.insert("$state".into(), Value::Object(state_meta));
        }

        Ok(result)
    }
}
