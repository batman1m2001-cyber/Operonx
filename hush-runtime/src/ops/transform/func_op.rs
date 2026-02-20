//! RustFuncRegistry — global registry of Rust-native op implementations.
//!
//! Maps op names to Rust functions that execute without Python callback.
//! The GIL is released during execution for true multi-core parallelism.

use std::sync::OnceLock;

use ahash::AHashMap;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

// =============================================================================
// RustOp trait — implemented by each Rust-native op
// =============================================================================

/// Trait for Rust-native op implementations.
///
/// Each implementation receives inputs as native Rust types (extracted from PyObject),
/// executes the computation (potentially with GIL released), and returns outputs.
pub trait RustOp: Send + Sync {
    /// Execute the op with the given inputs dict.
    /// The implementation should extract needed values from the dict,
    /// release GIL if doing CPU work, and return a Python dict of outputs.
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject>;
}

// =============================================================================
// Built-in ops
// =============================================================================

/// rust_double: x → result = x * 2
/// Accepts both int and float inputs.
struct DoubleOp;

impl RustOp for DoubleOp {
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let x_obj = inputs
            .get_item("x")?
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'x'"))?;

        let out = PyDict::new_bound(py);
        if let Ok(x) = x_obj.extract::<i64>() {
            let result = py.allow_threads(|| x * 2);
            out.set_item("result", result)?;
        } else {
            let x: f64 = x_obj.extract()?;
            let result = py.allow_threads(|| x * 2.0);
            out.set_item("result", result)?;
        }
        Ok(out.into())
    }
}

/// rust_add: a, b → result = a + b
/// Accepts both int and float inputs. If either input is float, result is float.
struct AddOp;

impl RustOp for AddOp {
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let a_obj = inputs
            .get_item("a")?
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'a'"))?;
        let b_obj = inputs
            .get_item("b")?
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'b'"))?;

        let out = PyDict::new_bound(py);
        match (a_obj.extract::<i64>(), b_obj.extract::<i64>()) {
            (Ok(a), Ok(b)) => {
                let result = py.allow_threads(|| a + b);
                out.set_item("result", result)?;
            }
            _ => {
                let a: f64 = a_obj.extract()?;
                let b: f64 = b_obj.extract()?;
                let result = py.allow_threads(|| a + b);
                out.set_item("result", result)?;
            }
        }
        Ok(out.into())
    }
}

/// rust_hash_chain: data, iterations → hash (CPU-heavy, demonstrates GIL release)
struct HashChainOp;

impl RustOp for HashChainOp {
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let data: String = inputs
            .get_item("data")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'data'")
            })?
            .extract()?;
        let iterations: i64 = inputs
            .get_item("iterations")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'iterations'")
            })?
            .extract()?;

        // CPU-heavy work with GIL released
        let hash = py.allow_threads(|| {
            use std::collections::hash_map::DefaultHasher;
            use std::hash::{Hash, Hasher};
            let mut current = data;
            for _ in 0..iterations {
                let mut hasher = DefaultHasher::new();
                current.hash(&mut hasher);
                current = format!("{:016x}", hasher.finish());
            }
            current
        });

        let out = PyDict::new_bound(py);
        out.set_item("hash", &hash)?;
        Ok(out.into())
    }
}

// =============================================================================
// Built-in string ops
// =============================================================================

/// rust_string_concat: parts (list of str) → result (str)
struct StringConcatOp;

impl RustOp for StringConcatOp {
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let parts_obj = inputs
            .get_item("parts")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'parts'")
            })?;
        let parts: &Bound<'_, PyList> = parts_obj.downcast()?;
        let strings: Vec<String> = parts
            .iter()
            .map(|item| item.extract::<String>())
            .collect::<PyResult<_>>()?;

        let result = py.allow_threads(|| strings.join(""));

        let out = PyDict::new_bound(py);
        out.set_item("result", result)?;
        Ok(out.into())
    }
}

/// rust_string_split: text, delimiter → parts (list of str)
struct StringSplitOp;

impl RustOp for StringSplitOp {
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let text: String = inputs
            .get_item("text")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'text'")
            })?
            .extract()?;
        let delimiter: String = inputs
            .get_item("delimiter")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'delimiter'")
            })?
            .extract()?;

        let parts: Vec<String> = py.allow_threads(|| {
            text.split(&delimiter).map(|s| s.to_string()).collect()
        });

        let out = PyDict::new_bound(py);
        let py_list = PyList::new_bound(py, &parts);
        out.set_item("parts", py_list)?;
        Ok(out.into())
    }
}

/// rust_string_template: template, vars → result (str)
/// Simple {key} substitution.
struct StringTemplateOp;

impl RustOp for StringTemplateOp {
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let template: String = inputs
            .get_item("template")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'template'")
            })?
            .extract()?;
        let vars_obj = inputs
            .get_item("vars")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'vars'")
            })?;
        let vars_dict: &Bound<'_, PyDict> = vars_obj.downcast()?;

        // Extract vars to Rust strings
        let mut replacements: Vec<(String, String)> = Vec::new();
        for (key, value) in vars_dict.iter() {
            let k: String = key.extract()?;
            let v: String = value.str()?.extract()?;
            replacements.push((k, v));
        }

        let result = py.allow_threads(|| {
            let mut output = template;
            for (key, value) in &replacements {
                output = output.replace(&format!("{{{}}}", key), value);
            }
            output
        });

        let out = PyDict::new_bound(py);
        out.set_item("result", result)?;
        Ok(out.into())
    }
}

// =============================================================================
// Built-in JSON ops
// =============================================================================

/// rust_json_parse: text → data (Python object)
struct JsonParseOp;

impl RustOp for JsonParseOp {
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let text: String = inputs
            .get_item("text")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'text'")
            })?
            .extract()?;

        let json_mod = py.import_bound("json")?;
        let data = json_mod.call_method1("loads", (&text,))?;

        let out = PyDict::new_bound(py);
        out.set_item("data", data)?;
        Ok(out.into())
    }
}

/// rust_json_extract: data (dict), path (dot-separated) → value
struct JsonExtractOp;

impl RustOp for JsonExtractOp {
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let data = inputs
            .get_item("data")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'data'")
            })?;
        let path: String = inputs
            .get_item("path")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'path'")
            })?
            .extract()?;

        // Walk the path
        let keys: Vec<&str> = path.split('.').collect();
        let mut current: PyObject = data.unbind();
        for key in keys {
            current = current.bind(py).get_item(key)?.unbind();
        }

        let out = PyDict::new_bound(py);
        out.set_item("value", current)?;
        Ok(out.into())
    }
}

/// rust_json_merge: a (dict), b (dict) → result (dict)
/// Shallow merge: b overwrites a.
struct JsonMergeOp;

impl RustOp for JsonMergeOp {
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let a = inputs
            .get_item("a")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'a'")
            })?;
        let b = inputs
            .get_item("b")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'b'")
            })?;

        let a_dict: &Bound<'_, PyDict> = a.downcast()?;
        let b_dict: &Bound<'_, PyDict> = b.downcast()?;

        let result = a_dict.copy()?;
        result.update(b_dict.as_mapping())?;

        let out = PyDict::new_bound(py);
        out.set_item("result", result)?;
        Ok(out.into())
    }
}

// =============================================================================
// Built-in math ops
// =============================================================================

/// rust_math_sum: values (list of numbers) → result
struct MathSumOp;

impl RustOp for MathSumOp {
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let values_obj = inputs
            .get_item("values")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'values'")
            })?;
        let values: &Bound<'_, PyList> = values_obj.downcast()?;
        let nums: Vec<f64> = values
            .iter()
            .map(|item| item.extract::<f64>())
            .collect::<PyResult<_>>()?;

        let result = py.allow_threads(|| nums.iter().sum::<f64>());

        let out = PyDict::new_bound(py);
        out.set_item("result", result)?;
        Ok(out.into())
    }
}

/// rust_math_mean: values (list of numbers) → result
struct MathMeanOp;

impl RustOp for MathMeanOp {
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let values_obj = inputs
            .get_item("values")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'values'")
            })?;
        let values: &Bound<'_, PyList> = values_obj.downcast()?;
        let nums: Vec<f64> = values
            .iter()
            .map(|item| item.extract::<f64>())
            .collect::<PyResult<_>>()?;

        let result = py.allow_threads(|| {
            if nums.is_empty() {
                0.0
            } else {
                nums.iter().sum::<f64>() / nums.len() as f64
            }
        });

        let out = PyDict::new_bound(py);
        out.set_item("result", result)?;
        Ok(out.into())
    }
}

/// rust_math_max: values (list of numbers) → result
struct MathMaxOp;

impl RustOp for MathMaxOp {
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let values_obj = inputs
            .get_item("values")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'values'")
            })?;
        let values: &Bound<'_, PyList> = values_obj.downcast()?;
        let nums: Vec<f64> = values
            .iter()
            .map(|item| item.extract::<f64>())
            .collect::<PyResult<_>>()?;

        let result = py.allow_threads(|| {
            nums.iter().copied().fold(f64::NEG_INFINITY, f64::max)
        });

        let out = PyDict::new_bound(py);
        out.set_item("result", result)?;
        Ok(out.into())
    }
}

/// rust_math_min: values (list of numbers) → result
struct MathMinOp;

impl RustOp for MathMinOp {
    fn execute(&self, py: Python, inputs: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let values_obj = inputs
            .get_item("values")?
            .ok_or_else(|| {
                PyErr::new::<pyo3::exceptions::PyKeyError, _>("missing input 'values'")
            })?;
        let values: &Bound<'_, PyList> = values_obj.downcast()?;
        let nums: Vec<f64> = values
            .iter()
            .map(|item| item.extract::<f64>())
            .collect::<PyResult<_>>()?;

        let result = py.allow_threads(|| {
            nums.iter().copied().fold(f64::INFINITY, f64::min)
        });

        let out = PyDict::new_bound(py);
        out.set_item("result", result)?;
        Ok(out.into())
    }
}

// =============================================================================
// Global registry singleton
// =============================================================================

/// Thread-safe global registry of Rust ops.
static GLOBAL_REGISTRY: OnceLock<AHashMap<String, Box<dyn RustOp>>> = OnceLock::new();

fn init_registry() -> AHashMap<String, Box<dyn RustOp>> {
    let mut ops: AHashMap<String, Box<dyn RustOp>> = AHashMap::new();
    // Phase 3: test ops
    ops.insert("rust_double".to_string(), Box::new(DoubleOp));
    ops.insert("rust_add".to_string(), Box::new(AddOp));
    ops.insert("rust_hash_chain".to_string(), Box::new(HashChainOp));
    // Phase 4: string ops
    ops.insert("rust_string_concat".to_string(), Box::new(StringConcatOp));
    ops.insert("rust_string_split".to_string(), Box::new(StringSplitOp));
    ops.insert(
        "rust_string_template".to_string(),
        Box::new(StringTemplateOp),
    );
    // Phase 4: JSON ops
    ops.insert("rust_json_parse".to_string(), Box::new(JsonParseOp));
    ops.insert("rust_json_extract".to_string(), Box::new(JsonExtractOp));
    ops.insert("rust_json_merge".to_string(), Box::new(JsonMergeOp));
    // Phase 4: math ops
    ops.insert("rust_math_sum".to_string(), Box::new(MathSumOp));
    ops.insert("rust_math_mean".to_string(), Box::new(MathMeanOp));
    ops.insert("rust_math_max".to_string(), Box::new(MathMaxOp));
    ops.insert("rust_math_min".to_string(), Box::new(MathMinOp));
    ops
}

fn get_registry() -> &'static AHashMap<String, Box<dyn RustOp>> {
    GLOBAL_REGISTRY.get_or_init(init_registry)
}

// =============================================================================
// Crate-internal registry access (no FFI crossing)
// =============================================================================

/// Execute a Rust op internally (used by CompiledGraph::try_execute_inline).
pub(crate) fn execute_internal(
    py: Python,
    name: &str,
    inputs: &Bound<'_, PyDict>,
) -> PyResult<Option<PyObject>> {
    let registry = get_registry();
    match registry.get(name) {
        Some(op) => {
            let result = op.execute(py, inputs)?;
            Ok(Some(result))
        }
        None => Ok(None),
    }
}

/// Check if a Rust op is registered internally.
pub(crate) fn has_internal(name: &str) -> bool {
    get_registry().contains_key(name)
}

// =============================================================================
// Python-exposed registry interface
// =============================================================================

/// Python-accessible interface to the global Rust op registry.
#[pyclass]
pub struct RustFuncRegistry;

#[pymethods]
impl RustFuncRegistry {
    /// Execute a registered Rust op by name.
    ///
    /// Args:
    ///     name: The registered op name (e.g. "rust_double")
    ///     inputs: Python dict of input values
    ///
    /// Returns:
    ///     Python dict of output values, or None if op not found.
    #[staticmethod]
    fn execute(py: Python, name: &str, inputs: &Bound<'_, PyDict>) -> PyResult<Option<PyObject>> {
        let registry = get_registry();
        match registry.get(name) {
            Some(op) => {
                let result = op.execute(py, inputs)?;
                Ok(Some(result))
            }
            None => Ok(None),
        }
    }

    /// Check if a Rust op is registered.
    #[staticmethod]
    fn has(name: &str) -> bool {
        get_registry().contains_key(name)
    }

    /// List all registered Rust op names.
    #[staticmethod]
    fn list_ops() -> Vec<String> {
        get_registry().keys().cloned().collect()
    }
}
