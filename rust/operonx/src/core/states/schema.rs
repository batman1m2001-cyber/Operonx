//! `StateSchema` — compile-time state structure for a workflow.
//!
//! Mirrors Python [`operonx/core/states/schema.py`](../../../../operonx/core/states/schema.py).
//! Each `(op_name, var_name)` pair gets a unique slot index, enabling O(1)
//! state access. Refs resolve at most 1 hop (see MIGRATION_rust.md §4b.6).
//!
//! # Phase 1 scope
//! Stub + core fields and accessors only. Building a schema from a [`GraphConfig`]
//! lands when the engine entry point is wired up in Phase 3.

use std::collections::{HashMap, HashSet};

use serde_json::Value;

use super::ref_::{RefConfig, StreamPolicy};

/// Compile-time state structure — slot indices for `(op, var)` pairs +
/// compiled pull/push refs + stream policies per input var.
#[derive(Debug, Clone, Default)]
pub struct StateSchema {
    /// Workflow name (usually the root graph's full name).
    pub name: String,

    /// `(op_name, var_name) → slot index` in `MemoryState._cells`.
    var_to_idx: HashMap<(String, String), usize>,

    /// Default value per slot; parallel to `var_to_idx`.
    defaults: Vec<Option<Value>>,

    /// Pull ref per slot — if set, reads from this slot chase the source (1 hop).
    pull_refs: Vec<Option<RefConfig>>,

    /// Push ref per slot — if set, writes to this slot also write the target (1 hop).
    push_refs: Vec<Option<RefConfig>>,

    /// Indices of "shared" slots — one value across all contexts (literal inputs, etc.).
    shared_indices: HashSet<usize>,

    /// Stream policies keyed by `(op_name, var_name)` — scheduler reads these
    /// to decide sequential vs parallel vs collect dispatch per input.
    stream_policies: HashMap<(String, String), StreamPolicy>,
}

impl StateSchema {
    /// Empty schema — to be populated via a future `build_from_config`.
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            var_to_idx: HashMap::new(),
            defaults: Vec::new(),
            pull_refs: Vec::new(),
            push_refs: Vec::new(),
            shared_indices: HashSet::new(),
            stream_policies: HashMap::new(),
        }
    }

    /// Total number of slots — matches the length of `MemoryState._cells`.
    pub fn slot_count(&self) -> usize {
        self.defaults.len()
    }

    /// Look up the slot index for `(op, var)`. Returns `None` if not registered.
    pub fn get_index(&self, op: &str, var: &str) -> Option<usize> {
        // Temporary allocation; will be tightened when hot-path profiling drives a better key.
        self.var_to_idx
            .get(&(op.to_string(), var.to_string()))
            .copied()
    }

    /// `true` if the slot is graph-scoped (single value across all contexts).
    pub fn is_shared(&self, idx: usize) -> bool {
        self.shared_indices.contains(&idx)
    }

    /// Default value for a slot (may be `None`).
    pub fn default_at(&self, idx: usize) -> Option<&Value> {
        self.defaults.get(idx).and_then(|v| v.as_ref())
    }

    /// Pull ref at a slot (if any).
    pub fn pull_ref_at(&self, idx: usize) -> Option<&RefConfig> {
        self.pull_refs.get(idx).and_then(|v| v.as_ref())
    }

    /// Push ref at a slot (if any).
    pub fn push_ref_at(&self, idx: usize) -> Option<&RefConfig> {
        self.push_refs.get(idx).and_then(|v| v.as_ref())
    }

    /// Stream policy for an input var, if the user marked it `.parallel()` or `.collect()`.
    pub fn stream_policy(&self, op: &str, var: &str) -> Option<StreamPolicy> {
        self.stream_policies
            .get(&(op.to_string(), var.to_string()))
            .copied()
    }

    // ── Crate-internal builders used by schema-construction logic (Phase 3+) ──

    #[doc(hidden)]
    pub(crate) fn register_slot(
        &mut self,
        op: impl Into<String>,
        var: impl Into<String>,
        default: Option<Value>,
        is_shared: bool,
    ) -> usize {
        let key = (op.into(), var.into());
        if let Some(&idx) = self.var_to_idx.get(&key) {
            return idx;
        }
        let idx = self.defaults.len();
        self.var_to_idx.insert(key, idx);
        self.defaults.push(default);
        self.pull_refs.push(None);
        self.push_refs.push(None);
        if is_shared {
            self.shared_indices.insert(idx);
        }
        idx
    }

    #[doc(hidden)]
    pub(crate) fn set_pull_ref(&mut self, idx: usize, pull: RefConfig) {
        if let Some(slot) = self.pull_refs.get_mut(idx) {
            *slot = Some(pull);
        }
    }

    #[doc(hidden)]
    pub(crate) fn set_push_ref(&mut self, idx: usize, push: RefConfig) {
        if let Some(slot) = self.push_refs.get_mut(idx) {
            *slot = Some(push);
        }
    }

    #[doc(hidden)]
    pub(crate) fn set_stream_policy(
        &mut self,
        op: impl Into<String>,
        var: impl Into<String>,
        policy: StreamPolicy,
    ) {
        self.stream_policies.insert((op.into(), var.into()), policy);
    }
}
