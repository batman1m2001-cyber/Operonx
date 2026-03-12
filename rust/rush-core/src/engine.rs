//! Rush — standalone Rust execution engine for Hush workflows.
//!
//! Takes a JSON config string (from `json.dumps(GraphOp.serialize())`),
//! parses into Rust config structs, and runs the graph.
//! Pure Rust — no PyO3, no GIL.

use std::sync::Arc;

use serde_json::Value;

use crate::config::GraphConfig;
use crate::error::RushError;
use crate::middleware::Middleware;
use crate::ops::graph::graph_op;
use crate::registry::OpRegistry;
use crate::states::state::EngineState;
use crate::tracing::flush_worker::FlushWorker;
use crate::tracing::tracer::Tracer;

/// Standalone Rust execution engine for Hush workflows.
///
/// Usage:
///   let engine = Rush::new(config_json)?;
///   engine.set_registry(my_registry);
///   let result = engine.run_json(inputs, request_id, user_id, session_id)?;
pub struct Rush {
    config: Arc<GraphConfig>,
    registry: Option<Arc<dyn OpRegistry>>,
    tracers: Vec<Arc<dyn Tracer>>,
    middleware: Vec<Box<dyn Middleware>>,
    flush_worker: FlushWorker,
    _force_timestamps: bool,
}

impl Rush {
    /// Create a new Rush engine from a JSON config string.
    pub fn new(config_json: &str) -> Result<Self, RushError> {
        let config = GraphConfig::from_json(config_json)?;
        Ok(Rush {
            config: Arc::new(config),
            registry: None,
            tracers: Vec::new(),
            middleware: Vec::new(),
            flush_worker: FlushWorker::new(),
            _force_timestamps: false,
        })
    }

    /// Create a new Rush engine from a pre-parsed config.
    /// Avoids JSON parsing overhead — use for hot paths (e.g. per-request in rush-serve).
    pub fn from_config(config: Arc<GraphConfig>) -> Self {
        Rush {
            config,
            registry: None,
            tracers: Vec::new(),
            middleware: Vec::new(),
            flush_worker: FlushWorker::new(),
            _force_timestamps: false,
        }
    }

    /// Register an external op registry for custom Rust op dispatch.
    pub fn set_registry(&mut self, registry: Arc<dyn OpRegistry>) {
        self.registry = Some(registry);
    }

    /// Register a tracer for automatic flush after each run.
    pub fn add_tracer(&mut self, tracer: impl Tracer + 'static) {
        self.tracers.push(Arc::new(tracer));
    }

    /// Register a pre-wrapped Arc<dyn Tracer>.
    pub fn add_tracer_arc(&mut self, tracer: Arc<dyn Tracer>) {
        self.tracers.push(tracer);
    }

    /// Add middleware to the engine. Returns &mut self for chaining.
    pub fn use_middleware(&mut self, mw: impl Middleware + 'static) -> &mut Self {
        self.middleware.push(Box::new(mw));
        self
    }

    /// Force per-op timing metadata ($start_time, $end_time, $duration_ms)
    /// even when no tracers are registered. Useful for debugging/profiling.
    pub fn enable_timestamps(&mut self) {
        self._force_timestamps = true;
    }

    /// Run the graph with JSON inputs and return JSON result.
    ///
    /// Pure Rust execution — no Python, no GIL.
    /// If tracers are registered, auto-flushes traces in the background.
    pub fn run_json(
        &self,
        inputs: Value,
        request_id: Option<String>,
        user_id: Option<String>,
        session_id: Option<String>,
    ) -> Result<Value, RushError> {
        // Apply before_run middleware (in order)
        let mut inputs = inputs;
        for mw in &self.middleware {
            mw.before_run(&mut inputs)?;
        }

        let mut raw_state = EngineState::new();
        raw_state.needs_timestamps = !self.tracers.is_empty() || self._force_timestamps;
        let state = Arc::new(raw_state);
        let context = "main";

        if let Some(ref rid) = request_id {
            state.set_request_id(rid.clone());
        }

        // 1. Store inputs under graph's full_name
        if let Value::Object(map) = inputs {
            for (key, value) in map {
                state.set(&self.config.full_name, &key, context, value);
            }
        }

        // 2. Run graph (with timing for root graph node)
        let graph_start = if state.needs_timestamps {
            Some(chrono::Utc::now())
        } else {
            None
        };
        let graph_perf_start = std::time::Instant::now();

        let stream_contexts = match graph_op::run_graph(&self.config, &state, context, &self.registry) {
            Ok(sc) => sc,
            Err(e) => {
                // on_error middleware (in reverse order)
                for mw in self.middleware.iter().rev() {
                    mw.on_error(&e);
                }
                return Err(e);
            }
        };

        // Store timing for root graph (TraceCollector needs $start_time to detect execution)
        if let Some(start) = graph_start {
            let end = chrono::Utc::now();
            let duration_ms = graph_perf_start.elapsed().as_secs_f64() * 1000.0;
            state.set(
                &self.config.full_name, "$start_time", context,
                Value::String(start.to_rfc3339()),
            );
            state.set(
                &self.config.full_name, "$end_time", context,
                Value::String(end.to_rfc3339()),
            );
            state.set(
                &self.config.full_name, "$duration_ms", context,
                serde_json::json!(duration_ms),
            );
        }

        // 3. Collect outputs (aggregates from stream contexts if generators were used)
        let mut result = graph_op::get_outputs(&self.config, &state, context, &stream_contexts)?;

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

        // 5. Auto-flush traces if tracers registered
        if !self.tracers.is_empty() {
            self.flush_worker.submit(
                self.tracers.clone(),
                self.config.clone(),
                state,
            );
        }

        // Apply after_run middleware (in reverse order)
        for mw in self.middleware.iter().rev() {
            mw.after_run(&mut result)?;
        }

        Ok(result)
    }

    /// Access the parsed config.
    pub fn config(&self) -> &GraphConfig {
        &self.config
    }
}
