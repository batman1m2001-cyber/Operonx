//! EngineState — concurrent key-value state with context dimension.
//!
//! Mirrors Python's `states/state.py` (MemoryState).
//! Maps (op_full_name, var_name, context_id) → PyObject.
//! Default context is "" (empty string) for non-iteration code.
//! Iteration contexts: "[0]", "[1]", nested: "[0].[0]", etc.
//!
//! Uses DashMap for concurrent read/write (parallel op execution)
//! and Mutex for append-only metadata (tags).

use dashmap::DashMap;
use std::sync::Mutex;

use pyo3::prelude::*;

pub(crate) struct EngineState {
    values: DashMap<(String, String, String), PyObject>,
    /// Dynamic tags collected from op results via `$tags` key.
    tags: Mutex<Vec<String>>,
    /// Request ID for tracing / streaming (set by engine.run()).
    request_id: Mutex<Option<String>>,
}

impl EngineState {
    pub(crate) fn new() -> Self {
        EngineState {
            values: DashMap::new(),
            tags: Mutex::new(Vec::new()),
            request_id: Mutex::new(None),
        }
    }

    /// Set the request ID (called by engine.run()).
    pub(crate) fn set_request_id(&self, id: String) {
        *self.request_id.lock().unwrap() = Some(id);
    }

    /// Get the request ID (used by streaming ops for STREAM_SERVICE).
    pub(crate) fn request_id(&self) -> Option<String> {
        self.request_id.lock().unwrap().clone()
    }

    /// Get a value from state. Returns an owned clone (safe for concurrent access).
    pub(crate) fn get(&self, py: Python, full_name: &str, var: &str, context: &str) -> Option<PyObject> {
        self.values
            .get(&(full_name.to_string(), var.to_string(), context.to_string()))
            .map(|entry| entry.value().clone_ref(py))
    }

    /// Set a value in state. Thread-safe via DashMap.
    pub(crate) fn set(&self, full_name: String, var: String, context: String, value: PyObject) {
        self.values.insert((full_name, var, context), value);
    }

    /// Add dynamic tags (deduplicated). Mirrors Python's `MemoryState.add_tags()`.
    pub(crate) fn add_tags(&self, tags: Vec<String>) {
        let mut locked = self.tags.lock().unwrap();
        for tag in tags {
            if !locked.contains(&tag) {
                locked.push(tag);
            }
        }
    }

    pub(crate) fn tags(&self) -> Vec<String> {
        self.tags.lock().unwrap().clone()
    }

    /// Snapshot all stored values. Used by engine.rs to export state for tracing.
    pub(crate) fn values_snapshot(&self, py: Python) -> Vec<((String, String, String), PyObject)> {
        self.values
            .iter()
            .map(|entry| (entry.key().clone(), entry.value().clone_ref(py)))
            .collect()
    }
}
