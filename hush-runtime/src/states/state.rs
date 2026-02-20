use ahash::AHashMap;
use pyo3::exceptions::PyKeyError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyTuple};

use super::cell::{Cell, DEFAULT_CONTEXT};

// =============================================================================
// RefInfo — stores resolved reference data from Python Ref objects
// =============================================================================

/// Holds the compiled data from a Python Ref (pull or push).
/// The fn_obj is the Python `Ref._fn` callable that transforms values.
struct RefInfo {
    idx: i32,
    fn_obj: PyObject,
    is_output: bool,
}

// =============================================================================
// RustMemoryState — Rust-backed drop-in replacement for Python MemoryState
// =============================================================================

/// High-performance workflow state with Rust-backed storage.
///
/// Drop-in replacement for Python `MemoryState`. All cell storage and
/// index lookups happen in Rust; ref resolution calls back to Python
/// `Ref._fn` callables only when needed (uncommon path).
///
/// Maps to `hush-core/hush/core/states/state.py`.
#[pyclass]
pub struct RustMemoryState {
    name: String,
    var_to_idx: AHashMap<(String, String), usize>,
    cells: Vec<Cell>,
    pull_refs: Vec<Option<RefInfo>>,
    push_refs: Vec<Option<RefInfo>>,
    execution_order: Vec<(String, String, String)>,
    user_id: String,
    session_id: String,
    request_id: String,
    tags: Vec<String>,
    py_schema: PyObject,
}

/// Normalize context: None → DEFAULT_CONTEXT.
#[inline]
fn normalize_ctx(ctx_obj: &Bound<'_, PyAny>) -> PyResult<String> {
    if ctx_obj.is_none() {
        Ok(DEFAULT_CONTEXT.to_string())
    } else {
        ctx_obj.extract::<String>()
    }
}

/// Extract a 3-tuple key (op, var, ctx) from a Python object.
#[inline]
fn extract_key_3(key: &Bound<'_, PyAny>) -> PyResult<(String, String, String)> {
    let tuple: &Bound<'_, PyTuple> = key.downcast()?;
    let op: String = tuple.get_item(0)?.extract()?;
    let var: String = tuple.get_item(1)?.extract()?;
    let ctx = normalize_ctx(&tuple.get_item(2)?)?;
    Ok((op, var, ctx))
}

#[pymethods]
impl RustMemoryState {
    /// Build a RustMemoryState from a Python StateSchema.
    ///
    /// Extracts var_to_idx, defaults, pull_refs, push_refs from the
    /// Python schema and creates Rust-backed storage.
    #[staticmethod]
    #[pyo3(signature = (schema, inputs=None, user_id=None, session_id=None, request_id=None))]
    fn from_schema(
        schema: &Bound<'_, PyAny>,
        inputs: Option<&Bound<'_, PyDict>>,
        user_id: Option<String>,
        session_id: Option<String>,
        request_id: Option<String>,
    ) -> PyResult<Self> {
        let py = schema.py();

        // Extract schema fields
        let name: String = schema.getattr("name")?.extract()?;
        let var_to_idx_obj = schema.getattr("_var_to_idx")?;
        let defaults_obj = schema.getattr("_defaults")?;
        let pull_refs_obj = schema.getattr("_pull_refs")?;
        let push_refs_obj = schema.getattr("_push_refs")?;

        // Build var_to_idx: Dict[(str,str), int] → AHashMap<(String,String), usize>
        let var_to_idx_dict: &Bound<'_, PyDict> = var_to_idx_obj.downcast()?;
        let mut var_to_idx: AHashMap<(String, String), usize> =
            AHashMap::with_capacity(var_to_idx_dict.len());

        for (key, value) in var_to_idx_dict.iter() {
            let tuple: &Bound<'_, PyTuple> = key.downcast()?;
            let op: String = tuple.get_item(0)?.extract()?;
            let var: String = tuple.get_item(1)?.extract()?;
            let idx: usize = value.extract()?;
            var_to_idx.insert((op, var), idx);
        }

        // Build cells from defaults
        let defaults_list: &Bound<'_, PyList> = defaults_obj.downcast()?;
        let num_cells = defaults_list.len();
        let mut cells: Vec<Cell> = Vec::with_capacity(num_cells);
        for item in defaults_list.iter() {
            cells.push(Cell::new(item.unbind()));
        }

        // Build pull_refs
        let pull_refs_list: &Bound<'_, PyList> = pull_refs_obj.downcast()?;
        let mut pull_refs: Vec<Option<RefInfo>> = Vec::with_capacity(num_cells);
        for item in pull_refs_list.iter() {
            if item.is_none() {
                pull_refs.push(None);
            } else {
                let idx: i32 = item.getattr("idx")?.extract()?;
                let fn_obj: PyObject = item.getattr("_fn")?.unbind();
                let is_output: bool = item.getattr("is_output")?.extract()?;
                pull_refs.push(Some(RefInfo {
                    idx,
                    fn_obj,
                    is_output,
                }));
            }
        }

        // Build push_refs
        let push_refs_list: &Bound<'_, PyList> = push_refs_obj.downcast()?;
        let mut push_refs: Vec<Option<RefInfo>> = Vec::with_capacity(num_cells);
        for item in push_refs_list.iter() {
            if item.is_none() {
                push_refs.push(None);
            } else {
                let idx: i32 = item.getattr("idx")?.extract()?;
                let fn_obj: PyObject = item.getattr("_fn")?.unbind();
                let is_output: bool = item.getattr("is_output")?.extract()?;
                push_refs.push(Some(RefInfo {
                    idx,
                    fn_obj,
                    is_output,
                }));
            }
        }

        // Generate UUIDs for missing IDs
        let uuid_mod = py.import_bound("uuid")?;
        let gen_uuid = || -> PyResult<String> {
            let u = uuid_mod.call_method0("uuid4")?;
            u.str()?.extract()
        };
        let user_id = match user_id {
            Some(id) => id,
            None => gen_uuid()?,
        };
        let session_id = match session_id {
            Some(id) => id,
            None => gen_uuid()?,
        };
        let request_id = match request_id {
            Some(id) => id,
            None => gen_uuid()?,
        };

        let py_schema = schema.clone().unbind();

        let mut state = RustMemoryState {
            name: name.clone(),
            var_to_idx,
            cells,
            pull_refs,
            push_refs,
            execution_order: Vec::new(),
            user_id,
            session_id,
            request_id,
            tags: Vec::new(),
            py_schema,
        };

        // Apply initial inputs
        if let Some(inputs_dict) = inputs {
            for (key, value) in inputs_dict.iter() {
                let var: String = key.extract()?;
                if let Some(&idx) = state.var_to_idx.get(&(name.clone(), var)) {
                    state.cells[idx].set(DEFAULT_CONTEXT, value.unbind());
                }
            }
        }

        Ok(state)
    }

    // =========================================================================
    // Core API: __getitem__ / __setitem__
    // =========================================================================

    /// state[op, var, ctx] = value
    ///
    /// Store value. Push to target if push_ref exists (1 hop only).
    fn __setitem__(&mut self, key: &Bound<'_, PyAny>, value: &Bound<'_, PyAny>) -> PyResult<()> {
        let py = key.py();
        let (op, var, ctx_key) = extract_key_3(key)?;

        let idx = self
            .var_to_idx
            .get(&(op.clone(), var.clone()))
            .copied()
            .ok_or_else(|| PyKeyError::new_err(format!("({}, {}) not in schema", op, var)))?;

        let value_py: PyObject = value.clone().unbind();
        self.cells[idx].set(&ctx_key, value_py.clone_ref(py));

        // Push ref: push 1 hop to target
        if let Some(ref push_ref) = self.push_refs[idx] {
            if push_ref.idx >= 0 {
                let target_idx = push_ref.idx as usize;
                let result = push_ref.fn_obj.call1(py, (&value_py,))?;
                self.cells[target_idx].set(&ctx_key, result);
            }
        }

        Ok(())
    }

    /// state[op, var, ctx] → value
    ///
    /// Get value. Pull from source if pull_ref exists (1 hop only).
    fn __getitem__(&mut self, key: &Bound<'_, PyAny>) -> PyResult<PyObject> {
        let py = key.py();
        let (op, var, ctx_key) = extract_key_3(key)?;

        let idx = match self.var_to_idx.get(&(op, var)) {
            Some(&i) => i,
            None => return Ok(py.None()),
        };

        // Has cached value? Return it
        if self.cells[idx].contains(&ctx_key) {
            return Ok(self.cells[idx].get(py, &ctx_key));
        }

        // Pull ref: pull 1 hop from source and cache
        // Extract ref data first to avoid borrow issues
        let pull_data = self.pull_refs[idx].as_ref().and_then(|r| {
            if !r.is_output && r.idx >= 0 {
                Some((r.idx as usize, r.fn_obj.clone_ref(py)))
            } else {
                None
            }
        });

        if let Some((source_idx, fn_obj)) = pull_data {
            let source_cell = &self.cells[source_idx];
            let has_value =
                source_cell.contains(&ctx_key) || !source_cell.default_value.is_none(py);
            if has_value {
                let source_value = source_cell.get(py, &ctx_key);
                let result = fn_obj.call1(py, (source_value,))?;
                // Cache the resolved value
                self.cells[idx].set(&ctx_key, result.clone_ref(py));
                return Ok(result);
            }
        }

        // No value — return default
        Ok(self.cells[idx].default_value.clone_ref(py))
    }

    /// Explicit parameter version of __getitem__.
    #[pyo3(signature = (op, var, ctx=None))]
    fn get(
        &mut self,
        py: Python,
        op: String,
        var: String,
        ctx: Option<String>,
    ) -> PyResult<PyObject> {
        let ctx_key = ctx.as_deref().unwrap_or(DEFAULT_CONTEXT);

        let idx = match self.var_to_idx.get(&(op, var)) {
            Some(&i) => i,
            None => return Ok(py.None()),
        };

        // Has cached value?
        if self.cells[idx].contains(ctx_key) {
            return Ok(self.cells[idx].get(py, ctx_key));
        }

        // Pull ref
        let pull_data = self.pull_refs[idx].as_ref().and_then(|r| {
            if !r.is_output && r.idx >= 0 {
                Some((r.idx as usize, r.fn_obj.clone_ref(py)))
            } else {
                None
            }
        });

        if let Some((source_idx, fn_obj)) = pull_data {
            let source_cell = &self.cells[source_idx];
            let has_value =
                source_cell.contains(ctx_key) || !source_cell.default_value.is_none(py);
            if has_value {
                let source_value = source_cell.get(py, ctx_key);
                let result = fn_obj.call1(py, (source_value,))?;
                self.cells[idx].set(ctx_key, result.clone_ref(py));
                return Ok(result);
            }
        }

        Ok(self.cells[idx].default_value.clone_ref(py))
    }

    /// Check if value exists (without resolving ref).
    #[pyo3(signature = (op, var, ctx=None))]
    fn has(&self, op: String, var: String, ctx: Option<String>) -> bool {
        let ctx_key = ctx.as_deref().unwrap_or(DEFAULT_CONTEXT);
        match self.var_to_idx.get(&(op, var)) {
            Some(&idx) => self.cells[idx].contains(ctx_key),
            None => false,
        }
    }

    // =========================================================================
    // Index-based access (bypass ref logic)
    // =========================================================================

    /// Raw cell access by index (no ref resolution).
    #[pyo3(signature = (idx, ctx=None))]
    fn get_by_index(&self, py: Python, idx: usize, ctx: Option<String>) -> PyResult<PyObject> {
        let ctx_key = ctx.as_deref().unwrap_or(DEFAULT_CONTEXT);
        if idx < self.cells.len() {
            Ok(self.cells[idx].get(py, ctx_key))
        } else {
            Err(pyo3::exceptions::PyIndexError::new_err(format!(
                "Index {} out of range",
                idx
            )))
        }
    }

    /// Raw cell assignment by index (no push ref).
    #[pyo3(signature = (idx, value, ctx=None))]
    fn set_by_index(
        &mut self,
        idx: usize,
        value: &Bound<'_, PyAny>,
        ctx: Option<String>,
    ) -> PyResult<()> {
        let ctx_key = ctx.as_deref().unwrap_or(DEFAULT_CONTEXT);
        if idx < self.cells.len() {
            self.cells[idx].set(ctx_key, value.clone().unbind());
            Ok(())
        } else {
            Err(pyo3::exceptions::PyIndexError::new_err(format!(
                "Index {} out of range",
                idx
            )))
        }
    }

    // =========================================================================
    // Execution tracking
    // =========================================================================

    /// Record op execution order for TraceCollector.
    #[pyo3(signature = (op_name, parent=None, context_id=None))]
    fn record_execution(
        &mut self,
        op_name: String,
        parent: Option<String>,
        context_id: Option<String>,
    ) {
        self.execution_order.push((
            op_name,
            parent.unwrap_or_default(),
            context_id.unwrap_or_default(),
        ));
    }

    // =========================================================================
    // Properties
    // =========================================================================

    #[getter]
    fn name(&self) -> &str {
        &self.name
    }

    #[getter]
    fn execution_order(&self, py: Python) -> PyResult<PyObject> {
        let result = PyList::empty_bound(py);
        for (op, parent, ctx) in &self.execution_order {
            let d = PyDict::new_bound(py);
            d.set_item("op", op)?;
            d.set_item("parent", parent)?;
            d.set_item("context_id", ctx)?;
            result.append(d)?;
        }
        Ok(result.unbind().into())
    }

    #[getter]
    fn metadata(&self, py: Python) -> PyResult<PyObject> {
        let d = PyDict::new_bound(py);
        d.set_item("user_id", &self.user_id)?;
        d.set_item("session_id", &self.session_id)?;
        d.set_item("request_id", &self.request_id)?;
        Ok(d.unbind().into())
    }

    #[getter]
    fn user_id(&self) -> &str {
        &self.user_id
    }

    #[getter]
    fn session_id(&self) -> &str {
        &self.session_id
    }

    #[getter]
    fn request_id(&self) -> &str {
        &self.request_id
    }

    #[getter]
    fn schema(&self, py: Python) -> PyObject {
        self.py_schema.clone_ref(py)
    }

    #[getter]
    fn tags(&self) -> Vec<String> {
        self.tags.clone()
    }

    /// Add a dynamic tag (duplicates are ignored).
    fn add_tag(&mut self, tag: String) {
        if !self.tags.contains(&tag) {
            self.tags.push(tag);
        }
    }

    /// Add multiple dynamic tags.
    fn add_tags(&mut self, tags: Vec<String>) {
        for tag in tags {
            self.add_tag(tag);
        }
    }

    // =========================================================================
    // Collection interface
    // =========================================================================

    /// Check if (op, var) exists in schema.
    fn __contains__(&self, key: &Bound<'_, PyAny>) -> PyResult<bool> {
        let tuple: &Bound<'_, PyTuple> = key.downcast()?;
        let op: String = tuple.get_item(0)?.extract()?;
        let var: String = tuple.get_item(1)?.extract()?;
        Ok(self.var_to_idx.contains_key(&(op, var)))
    }

    fn __len__(&self) -> usize {
        self.cells.len()
    }

    fn __repr__(&self) -> String {
        format!(
            "RustMemoryState(name='{}', cells={})",
            self.name,
            self.cells.len()
        )
    }

    fn __hash__(&self) -> u64 {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        let mut hasher = DefaultHasher::new();
        self.request_id.hash(&mut hasher);
        hasher.finish()
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    #[pyo3(signature = (_exc_type=None, _exc_val=None, _exc_tb=None))]
    fn __exit__(
        &self,
        _exc_type: Option<&Bound<'_, PyAny>>,
        _exc_val: Option<&Bound<'_, PyAny>>,
        _exc_tb: Option<&Bound<'_, PyAny>>,
    ) {
    }

    /// Debug display of current state values.
    fn show(&self, py: Python) -> PyResult<()> {
        println!("\n=== RustMemoryState: {} ===", self.name);

        // Build reverse index
        let idx_to_key: AHashMap<usize, (&str, &str)> = self
            .var_to_idx
            .iter()
            .map(|((op, var), &idx)| (idx, (op.as_str(), var.as_str())))
            .collect();

        for idx in 0..self.cells.len() {
            if let Some(&(op, var)) = idx_to_key.get(&idx) {
                let cell = &self.cells[idx];
                let has_pull = self
                    .pull_refs
                    .get(idx)
                    .map(|r| r.is_some())
                    .unwrap_or(false);

                if cell.contexts.is_empty() {
                    if has_pull {
                        println!("{}.{} -> pull_ref (no value yet)", op, var);
                    } else {
                        let default_str = cell.default_value.bind(py).repr()?.to_string();
                        println!("{}.{} -> {}", op, var, default_str);
                    }
                } else if cell.contexts.len() == 1 {
                    let (ctx, value) = cell.contexts.iter().next().unwrap();
                    let value_str = value.bind(py).repr()?.to_string();
                    let truncated = if value_str.len() > 50 {
                        format!("{}...", &value_str[..50])
                    } else {
                        value_str
                    };
                    println!("{}.{} [{}] = {}", op, var, ctx, truncated);
                } else {
                    println!("{}.{}:", op, var);
                    for (ctx, value) in &cell.contexts {
                        let value_str = value.bind(py).repr()?.to_string();
                        let truncated = if value_str.len() > 50 {
                            format!("{}...", &value_str[..50])
                        } else {
                            value_str
                        };
                        println!("  [{}] = {}", ctx, truncated);
                    }
                }
            }
        }
        Ok(())
    }
}

// =========================================================================
// Crate-internal helpers (used by CompiledGraph for branch resolution
// and Phase 5 inline execution)
// =========================================================================

impl RustMemoryState {
    /// Read a string value from state without ref resolution.
    ///
    /// Used by CompiledGraph to read branch targets directly from Rust,
    /// eliminating the Python callback for `op.get_target(state, ctx)`.
    pub(crate) fn get_string(&self, py: Python, op: &str, var: &str, ctx: &str) -> Option<String> {
        let idx = *self.var_to_idx.get(&(op.to_string(), var.to_string()))?;
        let cell = &self.cells[idx];
        let value = cell.get(py, ctx);
        value.extract::<String>(py).ok()
    }

    /// Read a PyObject value from state with pull-ref resolution.
    ///
    /// Replicates `__getitem__` logic: check cached value, pull from source
    /// if needed, fall back to default. Used by `try_execute_inline()`.
    pub(crate) fn get_value(&mut self, py: Python, op: &str, var: &str, ctx: &str) -> PyObject {
        let idx = match self.var_to_idx.get(&(op.to_string(), var.to_string())) {
            Some(&i) => i,
            None => return py.None(),
        };

        // Has cached value? Return it.
        if self.cells[idx].contains(ctx) {
            return self.cells[idx].get(py, ctx);
        }

        // Pull ref: pull 1 hop from source and cache
        let pull_data = self.pull_refs[idx].as_ref().and_then(|r| {
            if !r.is_output && r.idx >= 0 {
                Some((r.idx as usize, r.fn_obj.clone_ref(py)))
            } else {
                None
            }
        });

        if let Some((source_idx, fn_obj)) = pull_data {
            let source_cell = &self.cells[source_idx];
            let has_value =
                source_cell.contains(ctx) || !source_cell.default_value.is_none(py);
            if has_value {
                let source_value = source_cell.get(py, ctx);
                if let Ok(result) = fn_obj.call1(py, (source_value,)) {
                    self.cells[idx].set(ctx, result.clone_ref(py));
                    return result;
                }
            }
        }

        // No value — return default
        self.cells[idx].default_value.clone_ref(py)
    }

    /// Write a PyObject value to state with push-ref support.
    ///
    /// Replicates `__setitem__` logic: store value, push to target if
    /// push_ref exists. Used by `try_execute_inline()`.
    pub(crate) fn set_value(
        &mut self,
        py: Python,
        op: &str,
        var: &str,
        ctx: &str,
        value: PyObject,
    ) -> PyResult<()> {
        let idx = self
            .var_to_idx
            .get(&(op.to_string(), var.to_string()))
            .copied()
            .ok_or_else(|| PyKeyError::new_err(format!("({}, {}) not in schema", op, var)))?;

        self.cells[idx].set(ctx, value.clone_ref(py));

        // Push ref: push 1 hop to target
        if let Some(ref push_ref) = self.push_refs[idx] {
            if push_ref.idx >= 0 {
                let target_idx = push_ref.idx as usize;
                let result = push_ref.fn_obj.call1(py, (&value,))?;
                self.cells[target_idx].set(ctx, result);
            }
        }

        Ok(())
    }

    /// Record execution order internally (no FFI crossing).
    ///
    /// Equivalent to the Python `record_execution()` method.
    pub(crate) fn record_execution_internal(
        &mut self,
        op_name: &str,
        parent: &str,
        ctx: &str,
    ) {
        self.execution_order.push((
            op_name.to_string(),
            parent.to_string(),
            ctx.to_string(),
        ));
    }
}

// Unit tests that need Python are in hush-runtime/tests/test_state.py.
// Rust-only unit tests below (no Python::with_gil needed).
