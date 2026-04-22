//! [`label()`] — let generator ops name their own yields.
//!
//! Mirrors Python [`operon/core/tracing/labels.py`](../../../../operon/core/tracing/labels.py).
//! Python uses a `ContextVar` pair (active generator key + per-run label
//! store); Rust uses `tokio::task_local!` with the same two-level design.
//!
//! # Phase 7 scope
//! Public [`label()`] function is wired. The scheduler hook that rebinds
//! `_current_gen_key` / `_current_labels_store` at each op dispatch lands
//! when the collector integrates labels into the `stream_context`
//! `display_name` (currently the collector writes plain `"[N]"`).

use std::collections::HashMap;
use std::sync::Arc;

use parking_lot::Mutex;

use crate::core::states::cell::ContextId;

/// Per-run label store: `(op_full_name, ctx) → (labels, next_idx)`.
#[derive(Debug, Default)]
pub struct LabelsStore {
    entries: HashMap<(String, ContextId), LabelsEntry>,
}

#[derive(Debug, Default, Clone)]
struct LabelsEntry {
    pub labels: Vec<String>,
    pub next_idx: usize,
}

impl LabelsStore {
    pub fn new() -> Self {
        Self::default()
    }

    /// Read the labels recorded for `(op_full_name, ctx)`.
    pub fn labels_for(&self, op_full_name: &str, ctx: &ContextId) -> Vec<String> {
        self.entries
            .get(&(op_full_name.to_string(), ctx.clone()))
            .map(|e| e.labels.clone())
            .unwrap_or_default()
    }

    /// Record `name` at the current yield-slot for `(op_full_name, ctx)`.
    pub(crate) fn record(&mut self, op_full_name: &str, ctx: &ContextId, name: &str) {
        let key = (op_full_name.to_string(), ctx.clone());
        let entry = self.entries.entry(key).or_default();
        while entry.labels.len() <= entry.next_idx {
            entry.labels.push(String::new());
        }
        entry.labels[entry.next_idx] = name.to_string();
    }

    /// Advance the "next yield" index — called by the scheduler after each
    /// Frame from a generator op.
    pub(crate) fn advance_yield(&mut self, op_full_name: &str, ctx: &ContextId) {
        let key = (op_full_name.to_string(), ctx.clone());
        let entry = self.entries.entry(key).or_default();
        if entry.labels.is_empty() {
            entry.labels.push(String::new());
        }
        entry.next_idx += 1;
    }
}

tokio::task_local! {
    /// Active generator op key — set by the scheduler before invoking an op's
    /// body so `label()` inside the op can address its own yields.
    static CURRENT_GEN_KEY: Mutex<Option<(String, ContextId)>>;

    /// Shared labels store for the current run — multiple concurrent
    /// `engine.run()` invocations get independent stores.
    static CURRENT_LABELS_STORE: Arc<Mutex<LabelsStore>>;
}

/// Label the **next** yield of the currently-running generator op.
///
/// Cheap no-op outside an op body (or outside any Operon run) — safe to
/// sprinkle throughout library code without guards.
pub fn label(name: &str) {
    let Ok(gen_slot) = CURRENT_GEN_KEY.try_with(|slot| slot.lock().clone()) else {
        return;
    };
    let Ok(store) = CURRENT_LABELS_STORE.try_with(Arc::clone) else {
        return;
    };
    let Some((op, ctx)) = gen_slot else {
        return;
    };
    store.lock().record(&op, &ctx, name);
}

/// Helpers — **scheduler-internal**. Not part of the public API.
#[doc(hidden)]
pub mod internal {
    use super::*;

    /// Enter an op-execution scope — the future runs with a specific
    /// `(op_full_name, ctx)` bound so `label()` inside resolves cleanly.
    pub async fn with_gen_key<F, R>(
        op_full_name: String,
        ctx: ContextId,
        store: Arc<Mutex<LabelsStore>>,
        fut: F,
    ) -> R
    where
        F: std::future::Future<Output = R>,
    {
        CURRENT_LABELS_STORE
            .scope(store, async move {
                CURRENT_GEN_KEY
                    .scope(Mutex::new(Some((op_full_name, ctx))), fut)
                    .await
            })
            .await
    }

    /// Advance the yield index for `(op_full_name, ctx)` in the current store.
    pub fn advance_yield(op_full_name: &str, ctx: &ContextId) {
        let _ = CURRENT_LABELS_STORE.try_with(|store| {
            store.lock().advance_yield(op_full_name, ctx);
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn label_outside_scope_is_noop() {
        // No panic, no side effect.
        label("outside");
    }

    #[test]
    fn labels_store_records_and_advances() {
        let mut store = LabelsStore::new();
        let ctx = crate::core::states::cell::default_context();
        store.record("gen", &ctx, "first");
        store.advance_yield("gen", &ctx);
        store.record("gen", &ctx, "second");
        assert_eq!(store.labels_for("gen", &ctx), vec!["first", "second"]);
    }
}
