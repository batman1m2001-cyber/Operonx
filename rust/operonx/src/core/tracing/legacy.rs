//! Legacy → new pipeline adapters.
//!
//! Mirrors Python `core/tracing/legacy.py`. Bridges old `TraceFilter`
//! consumers to the new processor-chain shape so call sites can migrate
//! incrementally.
//!
//! Phase-1 status: minimal shim. `trace_filter_to_processors` ports the
//! field-mapping logic the callbot exercises (exclude_ops, skip_empty,
//! max_io_size). Richer rewriters defer to a follow-up that's only
//! needed if a downstream graph exercises them.

use std::sync::Arc;

use crate::core::tracing::pipeline::Processor;
use crate::core::tracing::processors::{DropEmpty, DropOps, TruncateIO};
use crate::core::tracing::trace_filter::TraceFilter;

/// Convert a legacy `TraceFilter` into an equivalent processor chain.
///
/// Mapping:
///   - `exclude_ops` → `DropOps`
///   - `skip_empty`  → `DropEmpty`
///   - `max_io_size` → `TruncateIO`
///
/// Fields that no longer apply under the flat event model
/// (`preserve_children_of`, `protected_types`, `exclude_kinds`,
/// `rewriters`) are silently dropped. Most callers using these were
/// filtering out scaffolding the new event model doesn't emit.
pub fn trace_filter_to_processors(tf: &TraceFilter) -> Vec<Arc<dyn Processor>> {
    let mut chain: Vec<Arc<dyn Processor>> = Vec::new();

    if !tf.exclude_ops.is_empty() {
        chain.push(Arc::new(DropOps::new(tf.exclude_ops.iter().cloned())));
    }
    if tf.skip_empty {
        chain.push(Arc::new(DropEmpty));
    }
    if tf.max_io_size > 0 {
        chain.push(Arc::new(TruncateIO::new(tf.max_io_size)));
    }

    chain
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_filter_produces_empty_chain() {
        let tf = TraceFilter::default();
        let chain = trace_filter_to_processors(&tf);
        assert!(chain.is_empty());
    }

    #[test]
    fn exclude_ops_adds_drop_ops() {
        let mut tf = TraceFilter::default();
        tf.exclude_ops.push("picker".to_string());
        let chain = trace_filter_to_processors(&tf);
        assert_eq!(chain.len(), 1);
        assert_eq!(chain[0].name(), "DropOps");
    }

    #[test]
    fn skip_empty_plus_max_io_size_compose() {
        let mut tf = TraceFilter::default();
        tf.skip_empty = true;
        tf.max_io_size = 500;
        let chain = trace_filter_to_processors(&tf);
        assert_eq!(chain.len(), 2);
        assert_eq!(chain[0].name(), "DropEmpty");
        assert_eq!(chain[1].name(), "TruncateIO");
    }
}
