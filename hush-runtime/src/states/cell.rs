use ahash::AHashMap;
use pyo3::prelude::*;

pub const DEFAULT_CONTEXT: &str = "main";

/// Internal cell storage for a single workflow variable.
///
/// Each variable can have values in multiple contexts (e.g., loop iterations).
/// Maps to `hush-core/hush/core/states/cell.py`.
pub struct Cell {
    pub contexts: AHashMap<String, PyObject>,
    pub default_value: PyObject,
}

impl Cell {
    pub fn new(default_value: PyObject) -> Self {
        Cell {
            contexts: AHashMap::new(),
            default_value,
        }
    }

    /// Get value for a context. Returns default_value if context not found.
    pub fn get(&self, py: Python, ctx: &str) -> PyObject {
        self.contexts
            .get(ctx)
            .map(|v| v.clone_ref(py))
            .unwrap_or_else(|| self.default_value.clone_ref(py))
    }

    /// Set value for a context.
    pub fn set(&mut self, ctx: &str, value: PyObject) {
        self.contexts.insert(ctx.to_string(), value);
    }

    /// Check if a context has a value (without fallback to default).
    pub fn contains(&self, ctx: &str) -> bool {
        self.contexts.contains_key(ctx)
    }

    /// Remove a context and return its value (or default).
    pub fn pop(&mut self, py: Python, ctx: &str) -> PyObject {
        self.contexts
            .remove(ctx)
            .unwrap_or_else(|| self.default_value.clone_ref(py))
    }
}
