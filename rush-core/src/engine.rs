//! Rush — standalone Rust execution engine for Hush workflows.
//!
//! Takes a JSON config string (from `json.dumps(GraphOp.serialize())`),
//! parses into Rust config structs, and runs the graph.
//! Pure Rust — no PyO3, no GIL.

use serde_json::Value;

use crate::config::GraphConfig;
use crate::error::RushError;
use crate::ops::graph::graph_op;
use crate::states::state::EngineState;

/// Standalone Rust execution engine for Hush workflows.
///
/// Usage:
///   let engine = Rush::new(config_json)?;
///   let result = engine.run_json(inputs, request_id, user_id, session_id)?;
pub struct Rush {
    config: GraphConfig,
}

impl Rush {
    /// Create a new Rush engine from a JSON config string.
    pub fn new(config_json: &str) -> Result<Self, RushError> {
        let config = GraphConfig::from_json(config_json)?;
        Ok(Rush { config })
    }

    /// Run the graph with JSON inputs and return JSON result.
    ///
    /// Pure Rust execution — no Python, no GIL.
    pub fn run_json(
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

        // 1. Store inputs under graph's full_name
        if let Value::Object(map) = inputs {
            for (key, value) in map {
                state.set(&self.config.full_name, &key, context, value);
            }
        }

        // 2. Run graph
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

    /// Access the parsed config.
    pub fn config(&self) -> &GraphConfig {
        &self.config
    }
}
