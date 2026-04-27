//! `MemoryState` — workflow state backed by [`Cell`]s, indexed via [`StateSchema`].
//!
//! Mirrors Python [`operonx/core/states/state.py`](../../../../operonx/core/states/state.py).
//! Each `(op_name, var_name)` resolves to a slot index in constant time; each slot
//! is a [`Cell`] that stores multi-context values with parent-walk on read.
//!
//! # Phase 1 scope
//! This pass lands the data structure + direct (non-ref-resolved) access.
//! Ref pull/push resolution during `get`/`set` lands alongside the full ref
//! evaluator in a later step.

use serde_json::Value;

use super::cell::{default_context, Cell, ContextId};
use super::schema::StateSchema;

/// Workflow state — one `Cell` per schema slot, plus request-scoped identifiers.
///
/// Construction populates one `Cell` per slot (shared flag propagated from the
/// schema). Initial inputs land on the root op's matching slots at the default
/// context.
#[derive(Debug)]
pub struct MemoryState {
    pub schema: StateSchema,
    cells: Vec<Cell>,

    pub user_id: String,
    pub session_id: String,
    pub request_id: String,

    /// Dynamic tags collected during execution (appended by ops returning `$tags`).
    pub tags: Vec<String>,

    /// When `false`, tracers are suppressed for this run — engine sets this
    /// after confirming no tracer is attached to skip collector overhead.
    pub tracing: bool,
}

impl MemoryState {
    /// Build a fresh state from a schema.
    ///
    /// `inputs` values are keyed by the **root op's** var names (the workflow's
    /// entry schema). IDs default to UUIDs if not supplied.
    pub fn new(
        schema: StateSchema,
        inputs: Option<serde_json::Map<String, Value>>,
        user_id: Option<String>,
        session_id: Option<String>,
        request_id: Option<String>,
    ) -> Self {
        // Pre-size cells with default values + shared flags taken from the schema.
        let mut cells: Vec<Cell> = (0..schema.slot_count())
            .map(|idx| Cell::new(schema.default_at(idx).cloned(), schema.is_shared(idx)))
            .collect();

        // Apply initial inputs at the root op, default context.
        if let Some(input_map) = inputs {
            let root_name = schema.name.clone();
            let root_ctx = default_context();
            for (var, val) in input_map {
                if let Some(idx) = schema.get_index(&root_name, &var) {
                    cells[idx].set(&root_ctx, val);
                }
            }
        }

        Self {
            schema,
            cells,
            user_id: user_id.unwrap_or_else(new_uuid),
            session_id: session_id.unwrap_or_else(new_uuid),
            request_id: request_id.unwrap_or_else(new_uuid),
            tags: Vec::new(),
            tracing: true,
        }
    }

    /// Get a value at `(op, var, ctx)` with parent-walk on the context tuple.
    ///
    /// Does **not** resolve pull refs — that's the job of the ref evaluator
    /// (lands in a later step). Callers reading via refs should go through the
    /// evaluator instead of this direct accessor.
    pub fn get(&self, op: &str, var: &str, ctx: &ContextId) -> Option<&Value> {
        let idx = self.schema.get_index(op, var)?;
        self.cells.get(idx).and_then(|cell| cell.get(ctx))
    }

    /// Set a value at `(op, var, ctx)`. Does **not** apply push refs.
    pub fn set(
        &mut self,
        op: &str,
        var: &str,
        ctx: &ContextId,
        value: Value,
    ) -> Result<(), StateError> {
        let idx = self
            .schema
            .get_index(op, var)
            .ok_or_else(|| StateError::UnknownSlot(op.to_string(), var.to_string()))?;
        self.cells
            .get_mut(idx)
            .ok_or(StateError::SlotIndexOutOfRange(idx))?
            .set(ctx, value);
        Ok(())
    }

    /// Direct slot-index access, skipping the `(op, var)` lookup.
    pub fn get_by_index(&self, idx: usize, ctx: &ContextId) -> Option<&Value> {
        self.cells.get(idx).and_then(|cell| cell.get(ctx))
    }

    /// Direct slot-index write, skipping the `(op, var)` lookup.
    pub fn set_by_index(&mut self, idx: usize, ctx: &ContextId, value: Value) -> Result<(), StateError> {
        self.cells
            .get_mut(idx)
            .ok_or(StateError::SlotIndexOutOfRange(idx))?
            .set(ctx, value);
        Ok(())
    }

    /// Append a tag (from op output `$tags`).
    pub fn add_tag(&mut self, tag: impl Into<String>) {
        let tag = tag.into();
        if !self.tags.contains(&tag) {
            self.tags.push(tag);
        }
    }

    /// Borrow the underlying cell vec — used by the trace collector.
    pub(crate) fn cells(&self) -> &[Cell] {
        &self.cells
    }
}

/// Errors raised by direct state accessors.
#[derive(Debug, thiserror::Error)]
pub enum StateError {
    #[error("state: unknown slot ({0}, {1}) — not registered in schema")]
    UnknownSlot(String, String),

    #[error("state: slot index {0} out of range")]
    SlotIndexOutOfRange(usize),
}

impl From<StateError> for crate::core::exceptions::OperonError {
    fn from(e: StateError) -> Self {
        crate::core::exceptions::OperonError::State(e.to_string())
    }
}

fn new_uuid() -> String {
    uuid::Uuid::new_v4().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn schema_with_var(op: &str, var: &str) -> StateSchema {
        let mut s = StateSchema::new(op.to_string());
        s.register_slot(op, var, None, false);
        s
    }

    #[test]
    fn initial_inputs_land_on_root_op() {
        let mut schema = StateSchema::new("main");
        schema.register_slot("main", "x", None, false);
        let mut inputs = serde_json::Map::new();
        inputs.insert("x".into(), json!(42));
        let state = MemoryState::new(schema, Some(inputs), None, None, None);

        let ctx = default_context();
        assert_eq!(state.get("main", "x", &ctx), Some(&json!(42)));
    }

    #[test]
    fn set_and_get_roundtrip() {
        let schema = schema_with_var("op_a", "result");
        let mut state = MemoryState::new(schema, None, None, None, None);
        let ctx = default_context();
        state.set("op_a", "result", &ctx, json!("hello")).unwrap();
        assert_eq!(state.get("op_a", "result", &ctx), Some(&json!("hello")));
    }

    #[test]
    fn unknown_slot_errors() {
        let schema = schema_with_var("op_a", "result");
        let mut state = MemoryState::new(schema, None, None, None, None);
        let err = state.set("op_b", "nope", &default_context(), json!(1)).unwrap_err();
        assert!(matches!(err, StateError::UnknownSlot(_, _)));
    }
}
