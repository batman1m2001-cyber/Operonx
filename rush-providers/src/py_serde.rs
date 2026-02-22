//! PyObject ↔ serde_json::Value conversion utilities.
//!
//! Used to bridge Python dicts/lists and Rust serde_json types
//! at the boundary between GIL-held code and GIL-free async execution.

use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString};
use serde_json::{Map, Value};

/// Convert a Python object to serde_json::Value.
///
/// Supports: None, bool, int, float, str, list, dict.
/// Unknown types fall back to str() representation.
pub fn py_to_json(py: Python, obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    if obj.is_none() {
        return Ok(Value::Null);
    }

    // bool must be checked before int (Python bool is a subclass of int)
    if let Ok(b) = obj.downcast::<PyBool>() {
        return Ok(Value::Bool(b.is_true()));
    }

    if obj.is_instance_of::<PyInt>() {
        if let Ok(i) = obj.extract::<i64>() {
            return Ok(Value::Number(i.into()));
        }
        // Large ints → float
        if let Ok(f) = obj.extract::<f64>() {
            return Ok(serde_json::Number::from_f64(f)
                .map(Value::Number)
                .unwrap_or(Value::Null));
        }
    }

    if obj.is_instance_of::<PyFloat>() {
        let f: f64 = obj.extract()?;
        return Ok(serde_json::Number::from_f64(f)
            .map(Value::Number)
            .unwrap_or(Value::Null));
    }

    if obj.is_instance_of::<PyString>() {
        let s: String = obj.extract()?;
        return Ok(Value::String(s));
    }

    if let Ok(list) = obj.downcast::<PyList>() {
        let arr: Vec<Value> = list
            .iter()
            .map(|item| py_to_json(py, &item))
            .collect::<PyResult<_>>()?;
        return Ok(Value::Array(arr));
    }

    if let Ok(dict) = obj.downcast::<PyDict>() {
        return pydict_to_json(py, dict);
    }

    // Fallback: convert to string representation
    let s = obj.str()?.extract::<String>()?;
    Ok(Value::String(s))
}

/// Convert a Python dict to serde_json::Value::Object.
pub fn pydict_to_json(py: Python, dict: &Bound<'_, PyDict>) -> PyResult<Value> {
    let mut map = Map::with_capacity(dict.len());
    for (k, v) in dict.iter() {
        let key: String = k.extract()?;
        map.insert(key, py_to_json(py, &v)?);
    }
    Ok(Value::Object(map))
}

/// Convert serde_json::Value to a Python object.
pub fn json_to_py(py: Python, val: &Value) -> PyResult<PyObject> {
    match val {
        Value::Null => Ok(py.None()),
        Value::Bool(b) => Ok(b.to_object(py)),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.to_object(py))
            } else {
                Ok(n.as_f64().unwrap_or(0.0).to_object(py))
            }
        }
        Value::String(s) => Ok(s.to_object(py)),
        Value::Array(arr) => {
            let items: Vec<PyObject> = arr
                .iter()
                .map(|v| json_to_py(py, v))
                .collect::<PyResult<_>>()?;
            Ok(PyList::new_bound(py, items).into())
        }
        Value::Object(map) => {
            let dict = PyDict::new_bound(py);
            for (k, v) in map {
                dict.set_item(k, json_to_py(py, v)?)?;
            }
            Ok(dict.into())
        }
    }
}

/// Convert a serde_json::Value::Object to a Python dict.
/// Returns a new PyDict containing all key-value pairs.
pub fn json_to_pydict(py: Python, val: &Value) -> PyResult<PyObject> {
    match val {
        Value::Object(_) => json_to_py(py, val),
        _ => {
            // Wrap non-object values in a dict under "result" key
            let dict = PyDict::new_bound(py);
            dict.set_item("result", json_to_py(py, val)?)?;
            Ok(dict.into())
        }
    }
}
