//! EngineState — concurrent key-value state with context dimension.
//!
//! Mirrors Python's `states/state.py` (MemoryState).
//! Maps (op_full_name, var_name, context_id) → Arc<serde_json::Value>.
//! Root context is "main". Stream contexts are dot-joined:
//! "main", "main.[0]", "main.[0].[1]", etc.
//!
//! `get()` walks up the context hierarchy (Cell fallback):
//! "main.[0].[1]" → "main.[0]" → "main" — so stream ops
//! inherit values from parent contexts automatically.
//!
//! Uses DashMap for concurrent read/write (parallel op execution),
//! lasso::ThreadedRodeo for string interning (zero-alloc key lookups),
//! and Arc<Value> for O(1) value cloning (critical for large LLM responses).

use std::sync::{Arc, Mutex};

use dashmap::DashMap;
use lasso::{Spur, ThreadedRodeo};
use serde_json::Value;

pub struct EngineState {
    interner: ThreadedRodeo,
    values: DashMap<(Spur, Spur, Spur), Arc<Value>>,
    /// Dynamic tags collected from op results via `$tags` key.
    tags: Mutex<Vec<String>>,
    /// Request ID for tracing (set by engine.run()).
    request_id: Mutex<Option<String>>,
}

impl EngineState {
    pub fn new() -> Self {
        EngineState {
            interner: ThreadedRodeo::default(),
            values: DashMap::new(),
            tags: Mutex::new(Vec::new()),
            request_id: Mutex::new(None),
        }
    }

    /// Set the request ID (called by engine.run()).
    pub fn set_request_id(&self, id: String) {
        *self.request_id.lock().unwrap() = Some(id);
    }

    /// Get the request ID (used for tracing metadata).
    pub fn request_id(&self) -> Option<String> {
        self.request_id.lock().unwrap().clone()
    }

    /// Get a value from state with context hierarchy fallback.
    ///
    /// Tries exact context first, then walks up the dot-separated hierarchy:
    /// `"main.[0].[1]"` → `"main.[0]"` → `"main"`.
    /// This lets stream contexts inherit values from parent contexts.
    pub fn get(&self, full_name: &str, var: &str, context: &str) -> Option<Arc<Value>> {
        let k1 = self.interner.get(full_name)?;
        let k2 = self.interner.get(var)?;

        // 1. Exact match
        if let Some(k3) = self.interner.get(context) {
            if let Some(entry) = self.values.get(&(k1, k2, k3)) {
                return Some(Arc::clone(entry.value()));
            }
        }

        // 2. Walk up: "main.[0].[1]" → "main.[0]" → "main"
        let mut ctx = context;
        while let Some(dot_pos) = ctx.rfind('.') {
            ctx = &ctx[..dot_pos];
            if let Some(k3) = self.interner.get(ctx) {
                if let Some(entry) = self.values.get(&(k1, k2, k3)) {
                    return Some(Arc::clone(entry.value()));
                }
            }
        }

        None
    }

    /// Set a value in state. Interns key strings (allocs only on first-seen string).
    /// Callers pass &str — no more .clone() / .to_string() at call sites.
    pub fn set(&self, full_name: &str, var: &str, context: &str, value: Value) {
        let k1 = self.interner.get_or_intern(full_name);
        let k2 = self.interner.get_or_intern(var);
        let k3 = self.interner.get_or_intern(context);
        self.values.insert((k1, k2, k3), Arc::new(value));
    }

    /// Add dynamic tags (deduplicated). Mirrors Python's `MemoryState.add_tags()`.
    pub fn add_tags(&self, tags: Vec<String>) {
        let mut locked = self.tags.lock().unwrap();
        for tag in tags {
            if !locked.contains(&tag) {
                locked.push(tag);
            }
        }
    }

    pub fn tags(&self) -> Vec<String> {
        self.tags.lock().unwrap().clone()
    }

    /// Get all var→value pairs for a given `full_name` and `context`.
    /// Used by auto-forwarding to collect terminal op outputs.
    pub fn get_all_with_prefix(&self, full_name: &str, context: &str) -> Vec<(String, Value)> {
        let k1 = match self.interner.get(full_name) {
            Some(k) => k,
            None => return Vec::new(),
        };
        let k3 = match self.interner.get(context) {
            Some(k) => k,
            None => return Vec::new(),
        };

        let mut result = Vec::new();
        for entry in self.values.iter() {
            let (ek1, ek2, ek3) = entry.key();
            if *ek1 == k1 && *ek3 == k3 {
                let var = self.interner.resolve(ek2).to_string();
                let value = (**entry.value()).clone();
                result.push((var, value));
            }
        }
        result
    }

    /// Iterate all (context, start_time) pairs where an op was executed.
    /// Used by TraceCollector to discover all execution records for a given op.
    pub fn iter_executed(&self, full_name: &str) -> Vec<(String, String)> {
        let k1 = match self.interner.get(full_name) {
            Some(k) => k,
            None => return vec![],
        };
        let k2 = match self.interner.get("$start_time") {
            Some(k) => k,
            None => return vec![],
        };

        let mut result = Vec::new();
        for entry in self.values.iter() {
            let (ek1, ek2, ek3) = entry.key();
            if *ek1 == k1 && *ek2 == k2 {
                let ctx = self.interner.resolve(ek3).to_string();
                if let Value::String(ref s) = **entry.value() {
                    result.push((ctx, s.clone()));
                }
            }
        }
        result
    }

    /// Get all non-$-prefixed var→value pairs for an op at a specific context.
    /// Used by TraceCollector to build inputs/outputs for TraceNode.
    pub fn get_vars_for_op(&self, full_name: &str, context: &str) -> Vec<(String, Value)> {
        let k1 = match self.interner.get(full_name) {
            Some(k) => k,
            None => return Vec::new(),
        };
        let k3 = match self.interner.get(context) {
            Some(k) => k,
            None => return Vec::new(),
        };

        let mut result = Vec::new();
        for entry in self.values.iter() {
            let (ek1, ek2, ek3) = entry.key();
            if *ek1 == k1 && *ek3 == k3 {
                let var = self.interner.resolve(ek2);
                if !var.starts_with('$') {
                    result.push((var.to_string(), (**entry.value()).clone()));
                }
            }
        }
        result
    }

    /// Get a `$`-prefixed metadata value for an op at a specific context.
    /// Used by TraceCollector for timing, cost, model, etc.
    pub fn get_meta(&self, full_name: &str, meta_key: &str, context: &str) -> Option<Value> {
        let k1 = self.interner.get(full_name)?;
        let k2 = self.interner.get(meta_key)?;
        let k3 = self.interner.get(context)?;
        self.values.get(&(k1, k2, k3)).map(|v| (**v).clone())
    }

    /// Snapshot all stored values as a nested JSON object.
    /// Format: {"op_name": {"var_name": {"context": value}}}.
    /// Used by engine.rs to build $state metadata for tracing.
    pub fn values_snapshot(&self) -> Value {
        let mut result = serde_json::Map::new();
        for entry in self.values.iter() {
            let (k1, k2, k3) = entry.key();
            let op = self.interner.resolve(k1);
            let var = self.interner.resolve(k2);
            let ctx = self.interner.resolve(k3);
            let value: &Value = entry.value().as_ref();

            let op_map = result
                .entry(op.to_string())
                .or_insert_with(|| Value::Object(serde_json::Map::new()));
            if let Value::Object(ref mut op_obj) = op_map {
                let var_map = op_obj
                    .entry(var.to_string())
                    .or_insert_with(|| Value::Object(serde_json::Map::new()));
                if let Value::Object(ref mut var_obj) = var_map {
                    var_obj.insert(ctx.to_string(), value.clone());
                }
            }
        }
        Value::Object(result)
    }
}
