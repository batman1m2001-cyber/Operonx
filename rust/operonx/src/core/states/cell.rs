//! `Cell` — multi-context value storage for one workflow variable.
//!
//! Mirrors Python [`operon/core/states/cell.py`](../../../../operon/core/states/cell.py).
//! Each workflow variable lives in multiple contexts (loop iterations, stream
//! items). `Cell` stores all of them and walks the parent-context hierarchy on
//! read when the exact context isn't found.
//!
//! Context is a variable-length tuple — see MIGRATION_rust.md §4b.4:
//! `("main",)` → `("main", "[0]")` → `("main", "[0]", "loop_1")`, etc.
//! `SmallVec<[String; 4]>` keeps the common (shallow) case stack-allocated.

use std::collections::HashMap;

use serde_json::Value;
use smallvec::SmallVec;

/// Default context: `("main",)`. Matches Python's `DEFAULT_CONTEXT`.
pub const DEFAULT_CONTEXT_ROOT: &str = "main";

/// A workflow context id — a variable-length tuple of strings.
pub type ContextId = SmallVec<[String; 4]>;

/// Build the default context (`("main",)`).
pub fn default_context() -> ContextId {
    let mut ctx = SmallVec::new();
    ctx.push(DEFAULT_CONTEXT_ROOT.to_string());
    ctx
}

/// Multi-context value storage for one variable.
///
/// Mirrors Python's `Cell`. Writes store under an exact context; reads walk
/// the parent-context chain back to the default context.
#[derive(Debug, Clone, Default)]
pub struct Cell {
    contexts: HashMap<ContextId, Value>,
    default_value: Option<Value>,
    is_shared: bool,
}

impl Cell {
    /// Create a new cell.
    pub fn new(default_value: Option<Value>, is_shared: bool) -> Self {
        Self {
            contexts: HashMap::new(),
            default_value,
            is_shared,
        }
    }

    /// Store `value` under `context`. If the cell is shared, all writes fold to
    /// the default context, so the value is visible across all iterations.
    pub fn set(&mut self, context: &ContextId, value: Value) {
        let key = if self.is_shared {
            default_context()
        } else {
            context.clone()
        };
        self.contexts.insert(key, value);
    }

    /// Read the value for `context`. Walks up the context hierarchy:
    /// `("main", "[0]", "[1]")` → `("main", "[0]")` → `("main",)` — returning
    /// the first match found, falling back to `default_value` if nothing hits.
    pub fn get(&self, context: &ContextId) -> Option<&Value> {
        let mut ctx: ContextId = if self.is_shared {
            default_context()
        } else {
            context.clone()
        };
        loop {
            if let Some(v) = self.contexts.get(&ctx) {
                return Some(v);
            }
            if ctx.is_empty() {
                break;
            }
            ctx.pop();
        }
        self.default_value.as_ref()
    }

    /// Remove a context's value and return what was there (or the default).
    pub fn pop(&mut self, context: &ContextId) -> Option<Value> {
        self.contexts.remove(context).or_else(|| self.default_value.clone())
    }

    /// `true` if an exact-match entry exists for this context (no parent walk).
    pub fn contains(&self, context: &ContextId) -> bool {
        self.contexts.contains_key(context)
    }

    /// Iterate all stored `(context, value)` pairs (no parent walk).
    pub fn iter(&self) -> impl Iterator<Item = (&ContextId, &Value)> {
        self.contexts.iter()
    }

    /// Number of distinct contexts currently stored.
    pub fn len(&self) -> usize {
        self.contexts.len()
    }

    /// `true` if no context-specific values have been written.
    pub fn is_empty(&self) -> bool {
        self.contexts.is_empty()
    }

    /// Whether this cell shares one value across all contexts.
    pub fn is_shared(&self) -> bool {
        self.is_shared
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn ctx(parts: &[&str]) -> ContextId {
        parts.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn parent_walk_finds_value_at_ancestor_context() {
        let mut cell = Cell::new(None, false);
        cell.set(&ctx(&["main"]), json!(42));
        // Look up at deeper context; should fall back to ("main",).
        let result = cell.get(&ctx(&["main", "[0]", "[1]"]));
        assert_eq!(result, Some(&json!(42)));
    }

    #[test]
    fn exact_context_beats_ancestor() {
        let mut cell = Cell::new(None, false);
        cell.set(&ctx(&["main"]), json!("outer"));
        cell.set(&ctx(&["main", "[0]"]), json!("inner"));
        assert_eq!(cell.get(&ctx(&["main", "[0]"])), Some(&json!("inner")));
        assert_eq!(cell.get(&ctx(&["main"])), Some(&json!("outer")));
    }

    #[test]
    fn shared_cell_folds_writes_to_default() {
        let mut cell = Cell::new(None, true);
        cell.set(&ctx(&["main", "[0]"]), json!(1));
        cell.set(&ctx(&["main", "[1]"]), json!(2));
        // Both writes landed on ("main",) due to is_shared.
        assert_eq!(cell.len(), 1);
        assert_eq!(cell.get(&ctx(&["main", "[0]"])), Some(&json!(2)));
        assert_eq!(cell.get(&ctx(&["main", "[1]"])), Some(&json!(2)));
    }

    #[test]
    fn default_value_returned_when_nothing_found() {
        let cell = Cell::new(Some(json!("fallback")), false);
        assert_eq!(cell.get(&ctx(&["main"])), Some(&json!("fallback")));
    }
}
