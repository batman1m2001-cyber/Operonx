//! Config parsing — deserialize Python config dict into Rust structs.
//!
//! The config dict is produced by `GraphOp.serialize()` in Python (Phase 2).
//! This module parses it into typed Rust structs for the RustHush engine.

use ahash::{AHashMap, AHashSet};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rush_providers::config::ProviderConfig;
use smallvec::SmallVec;

// =============================================================================
// Config structs
// =============================================================================

/// Edge in the compiled adjacency list.
#[derive(Clone)]
pub struct AdjEntry {
    pub target: String,
    pub is_soft: bool,
}

/// Parsed graph configuration.
pub struct GraphConfig {
    pub name: String,
    pub full_name: String,
    pub ops: AHashMap<String, OpConfig>,
    pub entries: Vec<String>,
    pub initial_ready_count: AHashMap<String, i32>,
    pub compiled_adj: AHashMap<String, SmallVec<[AdjEntry; 4]>>,
    pub has_soft_preds: AHashSet<String>,
    pub inputs: Vec<ParamConfig>,
    pub outputs: Vec<ParamConfig>,
}

/// Execution bound hint — tells the scheduler how to schedule this op.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OpBound {
    /// I/O-bound: HTTP calls, LLM, embeddings. Use tokio::spawn (async).
    Io,
    /// CPU-bound: computation, transforms. Use rayon (parallel threads).
    Cpu,
}

/// Per-op configuration.
pub struct OpConfig {
    pub op_type: String,
    pub full_name: String,
    pub name: String,
    pub rust_op: Option<String>,
    pub python_callable: Option<PyObject>,
    pub is_async: bool,
    pub enabled: bool,
    pub verbose: bool,
    /// Whether this op uses streaming mode (LLM streaming with STREAM_SERVICE).
    pub stream: bool,
    /// Execution bound hint for scheduler dispatch.
    pub bound: OpBound,
    pub inputs: Vec<ParamConfig>,
    pub outputs: Vec<ParamConfig>,
    /// Branch-specific config (only for type == "branch").
    pub branch_config: Option<BranchConfig>,
    /// Nested graph config (for type == "graph", "for", "while").
    pub inner_graph: Option<Box<GraphConfig>>,
    /// Iteration config (for type == "for", "while").
    pub iteration_config: Option<IterationConfig>,
    /// Provider config (for type == "llm", "embedding", "rerank").
    pub provider_config: Option<ProviderConfig>,
}

/// Branch op configuration — conditions and targets.
pub struct BranchConfig {
    pub cases: Vec<BranchCase>,
    pub default: Option<String>,
}

/// A single branch case: condition ref + target op name.
pub struct BranchCase {
    pub condition: RefConfig,
    pub target: String,
}

/// Iteration op configuration (ForOp, WhileOp, MapOp, AIterOp).
pub struct IterationConfig {
    pub each: Vec<IterParamConfig>,
    pub broadcast: Vec<IterParamConfig>,
    pub fail_fast: bool,
    pub until: Option<String>,
    pub max_iterations: Option<usize>,
    /// Max concurrent iterations (MapOp, AIterOp). Defaults to CPU count.
    pub max_concurrency: Option<usize>,
    /// Async callback called per result in order (AIterOp only).
    pub callback: Option<PyObject>,
    /// Batch flush predicate: (batch, new_item) → bool (AIterOp only).
    pub batch_fn: Option<PyObject>,
}

/// A single iteration parameter (each or broadcast).
pub struct IterParamConfig {
    pub var_name: String,
    pub ref_config: Option<RefConfig>,
    pub literal: Option<PyObject>,
}

/// A single input/output parameter.
pub struct ParamConfig {
    pub var_name: String,
    pub ref_config: Option<RefConfig>,
    pub literal: Option<PyObject>,
    pub default_value: Option<PyObject>,
    pub required: bool,
}

/// A serialized Ref — variable reference with ops chain.
pub struct RefConfig {
    pub source: String,
    pub var: String,
    pub ops: Vec<RefOp>,
    pub is_output: bool,
}

/// A single operation in a Ref's ops chain.
pub struct RefOp {
    pub name: String,
    pub args: Vec<RefArg>,
}

/// An argument to a RefOp.
pub enum RefArg {
    Literal(PyObject),
    NestedRef(RefConfig),
    Callable(PyObject),
}

// =============================================================================
// Parsing from Python dict
// =============================================================================

impl GraphConfig {
    /// Parse a GraphConfig from a Python dict (output of `GraphOp.serialize()`).
    pub fn from_dict(py: Python, dict: &Bound<'_, PyDict>) -> PyResult<Self> {
        let name: String = get_string(dict, "name")?;
        let full_name: String = get_string(dict, "full_name")?;

        // Parse ops
        let ops_obj = dict
            .get_item("ops")?
            .ok_or_else(|| pyerr("missing 'ops'"))?;
        let ops_dict: &Bound<'_, PyDict> = ops_obj.downcast()?;

        let mut ops = AHashMap::with_capacity(ops_dict.len());
        for (k, v) in ops_dict.iter() {
            let op_name: String = k.extract()?;
            let op_dict: &Bound<'_, PyDict> = v.downcast()?;
            let op_config = OpConfig::from_dict(py, op_dict)?;
            ops.insert(op_name, op_config);
        }

        // Parse entries
        let entries_obj = dict
            .get_item("entries")?
            .ok_or_else(|| pyerr("missing 'entries'"))?;
        let entries: Vec<String> = entries_obj.extract()?;

        // Parse initial_ready_count
        let rc_obj = dict
            .get_item("initial_ready_count")?
            .ok_or_else(|| pyerr("missing 'initial_ready_count'"))?;
        let rc_dict: &Bound<'_, PyDict> = rc_obj.downcast()?;
        let mut initial_ready_count = AHashMap::with_capacity(rc_dict.len());
        for (k, v) in rc_dict.iter() {
            initial_ready_count.insert(k.extract::<String>()?, v.extract::<i32>()?);
        }

        // Parse compiled_adj
        let adj_obj = dict
            .get_item("compiled_adj")?
            .ok_or_else(|| pyerr("missing 'compiled_adj'"))?;
        let adj_dict: &Bound<'_, PyDict> = adj_obj.downcast()?;
        let mut compiled_adj = AHashMap::with_capacity(adj_dict.len());
        for (k, v) in adj_dict.iter() {
            let op_name: String = k.extract()?;
            let succs: &Bound<'_, PyList> = v.downcast()?;
            let mut entries_vec: SmallVec<[AdjEntry; 4]> = SmallVec::new();
            for item in succs.iter() {
                let pair: &Bound<'_, PyList> = item.downcast()?;
                let target: String = pair.get_item(0)?.extract()?;
                let is_soft: bool = pair.get_item(1)?.extract()?;
                entries_vec.push(AdjEntry { target, is_soft });
            }
            compiled_adj.insert(op_name, entries_vec);
        }

        // Parse has_soft_preds
        let has_soft_preds = match dict.get_item("has_soft_preds")? {
            Some(obj) if !obj.is_none() => {
                let list: Vec<String> = obj.extract()?;
                list.into_iter().collect::<AHashSet<String>>()
            }
            _ => AHashSet::new(),
        };

        // Parse graph-level inputs/outputs
        let inputs = parse_params(py, dict, "inputs")?;
        let outputs = parse_params(py, dict, "outputs")?;

        Ok(GraphConfig {
            name,
            full_name,
            ops,
            entries,
            initial_ready_count,
            compiled_adj,
            has_soft_preds,
            inputs,
            outputs,
        })
    }
}

impl OpConfig {
    /// Parse an OpConfig from a Python dict (output of `BaseOp.serialize()`).
    fn from_dict(py: Python, dict: &Bound<'_, PyDict>) -> PyResult<Self> {
        let op_type: String = get_string(dict, "type")?;
        let full_name: String = get_string(dict, "full_name")?;
        let name: String = get_string(dict, "name")?;

        // rust_op: Optional[str]
        let rust_op = dict
            .get_item("rust_op")?
            .and_then(|v| if v.is_none() { None } else { v.extract::<String>().ok() });

        // python_callable: Optional[PyObject]
        let python_callable = dict
            .get_item("python_callable")?
            .and_then(|v| if v.is_none() { None } else { Some(v.unbind()) });

        // is_async: bool
        let is_async: bool = dict
            .get_item("is_async")?
            .map(|v| v.extract::<bool>().unwrap_or(false))
            .unwrap_or(false);

        // enabled: bool (default true — all ops run unless explicitly disabled)
        let enabled: bool = dict
            .get_item("enabled")?
            .map(|v| v.extract::<bool>().unwrap_or(true))
            .unwrap_or(true);

        // verbose: bool (default false — no per-op logging)
        let verbose: bool = dict
            .get_item("verbose")?
            .map(|v| v.extract::<bool>().unwrap_or(false))
            .unwrap_or(false);

        // stream: bool (default false — LLM streaming mode)
        let stream: bool = dict
            .get_item("stream")?
            .map(|v| v.extract::<bool>().unwrap_or(false))
            .unwrap_or(false);

        // bound: "io" | "cpu" (default "cpu")
        let bound = match dict
            .get_item("bound")?
            .and_then(|v| if v.is_none() { None } else { v.extract::<String>().ok() })
            .as_deref()
        {
            Some("io") => OpBound::Io,
            _ => OpBound::Cpu,
        };

        let inputs = parse_params(py, dict, "inputs")?;
        let outputs = parse_params(py, dict, "outputs")?;

        // Parse branch config (only for type == "branch")
        let branch_config = if op_type == "branch" {
            Some(parse_branch_config(py, dict)?)
        } else {
            None
        };

        // Parse inner graph (for "graph", "for", "while", "map", "stream" types)
        // These ops' serialized dicts ARE GraphConfig — they have ops, entries, etc.
        let inner_graph = if matches!(op_type.as_str(), "graph" | "for" | "while" | "map" | "stream") {
            Some(Box::new(GraphConfig::from_dict(py, dict)?))
        } else {
            None
        };

        // Parse iteration config (for "for", "while", "map", "stream" types)
        let iteration_config = if matches!(op_type.as_str(), "for" | "while" | "map" | "stream") {
            Some(parse_iteration_config(py, dict, &op_type)?)
        } else {
            None
        };

        // Parse provider config (for "llm", "embedding", "rerank" types)
        let provider_config = rush_providers::config::parse_provider_config(py, &op_type, dict)?;

        Ok(OpConfig {
            op_type,
            full_name,
            name,
            rust_op,
            python_callable,
            is_async,
            enabled,
            verbose,
            stream,
            bound,
            inputs,
            outputs,
            branch_config,
            inner_graph,
            iteration_config,
            provider_config,
        })
    }
}

// =============================================================================
// Branch config parsing
// =============================================================================

/// Parse branch config from an op dict (cases + default).
/// Format: {"cases": [{"condition": {ref_dict}, "target": str}, ...], "default": str|None}
fn parse_branch_config(py: Python, dict: &Bound<'_, PyDict>) -> PyResult<BranchConfig> {
    // Parse cases
    let cases_obj = dict
        .get_item("cases")?
        .ok_or_else(|| pyerr("branch op missing 'cases'"))?;
    let cases_list: &Bound<'_, PyList> = cases_obj.downcast()?;

    let mut cases = Vec::with_capacity(cases_list.len());
    for item in cases_list.iter() {
        let case_dict: &Bound<'_, PyDict> = item.downcast()?;

        // Parse condition ref
        let cond_obj = case_dict
            .get_item("condition")?
            .ok_or_else(|| pyerr("branch case missing 'condition'"))?;
        let cond_dict: &Bound<'_, PyDict> = cond_obj.downcast()?;
        let condition = parse_ref_config(py, cond_dict)?;

        // Parse target name
        let target: String = get_string(case_dict, "target")?;

        cases.push(BranchCase { condition, target });
    }

    // Parse default (optional)
    let default = dict
        .get_item("default")?
        .and_then(|v| if v.is_none() { None } else { v.extract::<String>().ok() });

    Ok(BranchConfig { cases, default })
}

// =============================================================================
// Iteration config parsing
// =============================================================================

/// Parse iteration config from an op dict (each, broadcast, fail_fast, until, max_iterations).
/// Format: {"each": {"var": {"ref": {...}} | {"literal": val}}, "broadcast": {...}, ...}
fn parse_iteration_config(
    py: Python,
    dict: &Bound<'_, PyDict>,
    op_type: &str,
) -> PyResult<IterationConfig> {
    let each = parse_iter_params(py, dict, "each")?;
    let broadcast = parse_iter_params(py, dict, "broadcast")?;

    let fail_fast = if matches!(op_type, "for" | "map") {
        dict.get_item("fail_fast")?
            .map(|v| v.extract::<bool>().unwrap_or(false))
            .unwrap_or(false)
    } else {
        false
    };

    let until = if op_type == "while" {
        dict.get_item("until")?
            .and_then(|v| if v.is_none() { None } else { v.extract::<String>().ok() })
    } else {
        None
    };

    let max_iterations = if op_type == "while" {
        dict.get_item("max_iterations")?
            .and_then(|v| v.extract::<usize>().ok())
    } else {
        None
    };

    let max_concurrency = if matches!(op_type, "map" | "stream") {
        dict.get_item("max_concurrency")?
            .and_then(|v| if v.is_none() { None } else { v.extract::<usize>().ok() })
    } else {
        None
    };

    let callback = if op_type == "stream" {
        dict.get_item("callback")?
            .and_then(|v| if v.is_none() { None } else { Some(v.unbind()) })
    } else {
        None
    };

    let batch_fn = if op_type == "stream" {
        dict.get_item("batch_fn")?
            .and_then(|v| if v.is_none() { None } else { Some(v.unbind()) })
    } else {
        None
    };

    Ok(IterationConfig {
        each,
        broadcast,
        fail_fast,
        until,
        max_iterations,
        max_concurrency,
        callback,
        batch_fn,
    })
}

/// Parse an "each" or "broadcast" dict into Vec<IterParamConfig>.
/// Format: {"var_name": {"ref": {...}} | {"literal": val}}
fn parse_iter_params(
    py: Python,
    parent: &Bound<'_, PyDict>,
    key: &str,
) -> PyResult<Vec<IterParamConfig>> {
    let obj = match parent.get_item(key)? {
        Some(v) if !v.is_none() => v,
        _ => return Ok(Vec::new()),
    };

    let params_dict: &Bound<'_, PyDict> = obj.downcast()?;
    let mut result = Vec::with_capacity(params_dict.len());

    for (k, v) in params_dict.iter() {
        let var_name: String = k.extract()?;
        let entry: &Bound<'_, PyDict> = v.downcast()?;

        let ref_config = match entry.get_item("ref")? {
            Some(ref_obj) if !ref_obj.is_none() => {
                let ref_dict: &Bound<'_, PyDict> = ref_obj.downcast()?;
                Some(parse_ref_config(py, ref_dict)?)
            }
            _ => None,
        };

        let literal = entry
            .get_item("literal")?
            .and_then(|v| if v.is_none() { None } else { Some(v.unbind()) });

        result.push(IterParamConfig {
            var_name,
            ref_config,
            literal,
        });
    }

    Ok(result)
}

// =============================================================================
// Param / Ref parsing helpers
// =============================================================================

/// Parse a "inputs" or "outputs" dict from a config dict.
/// Format: {"var_name": {"ref": {...}|None, "literal": ...|None, "default": ...|None, "required": bool}}
fn parse_params(py: Python, parent: &Bound<'_, PyDict>, key: &str) -> PyResult<Vec<ParamConfig>> {
    let obj = match parent.get_item(key)? {
        Some(v) => v,
        None => return Ok(Vec::new()),
    };

    if obj.is_none() {
        return Ok(Vec::new());
    }

    let params_dict: &Bound<'_, PyDict> = obj.downcast()?;
    let mut result = Vec::with_capacity(params_dict.len());

    for (k, v) in params_dict.iter() {
        let var_name: String = k.extract()?;
        let entry: &Bound<'_, PyDict> = v.downcast()?;

        // Parse ref
        let ref_config = match entry.get_item("ref")? {
            Some(ref_obj) if !ref_obj.is_none() => {
                let ref_dict: &Bound<'_, PyDict> = ref_obj.downcast()?;
                Some(parse_ref_config(py, ref_dict)?)
            }
            _ => None,
        };

        // Parse literal
        let literal = entry
            .get_item("literal")?
            .and_then(|v| if v.is_none() { None } else { Some(v.unbind()) });

        // Parse default
        let default_value = entry
            .get_item("default")?
            .and_then(|v| if v.is_none() { None } else { Some(v.unbind()) });

        // Parse required
        let required: bool = entry
            .get_item("required")?
            .map(|v| v.extract::<bool>().unwrap_or(false))
            .unwrap_or(false);

        result.push(ParamConfig {
            var_name,
            ref_config,
            literal,
            default_value,
            required,
        });
    }

    Ok(result)
}

/// Parse a RefConfig from a Python dict (output of `Ref.serialize()`).
/// Format: {"source": str, "var": str, "ops": [[name, [args...]], ...], "is_output": bool}
fn parse_ref_config(py: Python, dict: &Bound<'_, PyDict>) -> PyResult<RefConfig> {
    let source: String = get_string(dict, "source")?;
    let var: String = get_string(dict, "var")?;
    let is_output: bool = dict
        .get_item("is_output")?
        .map(|v| v.extract::<bool>().unwrap_or(false))
        .unwrap_or(false);

    // Parse ops: [[name, [args...]], ...]
    let ops_obj = dict
        .get_item("ops")?
        .ok_or_else(|| pyerr("missing 'ops' in ref config"))?;
    let ops_list: &Bound<'_, PyList> = ops_obj.downcast()?;

    let mut ops = Vec::with_capacity(ops_list.len());
    for item in ops_list.iter() {
        let pair: &Bound<'_, PyList> = item.downcast()?;
        let op_name: String = pair.get_item(0)?.extract()?;
        let args_obj = pair.get_item(1)?;
        let args_list: &Bound<'_, PyList> = args_obj.downcast()?;

        let mut args = Vec::with_capacity(args_list.len());
        for arg in args_list.iter() {
            let ref_arg = parse_ref_arg(py, &arg)?;
            args.push(ref_arg);
        }

        ops.push(RefOp { name: op_name, args });
    }

    Ok(RefConfig {
        source,
        var,
        ops,
        is_output,
    })
}

/// Parse a single RefArg from a Python object.
/// Can be: {"__ref__": {...}} for nested Ref, {"__callable__": fn} for callable, or a literal.
fn parse_ref_arg(py: Python, obj: &Bound<'_, PyAny>) -> PyResult<RefArg> {
    // Check if it's a dict with __ref__ or __callable__ sentinel
    if let Ok(dict) = obj.downcast::<PyDict>() {
        if let Some(ref_val) = dict.get_item("__ref__")? {
            let ref_dict: &Bound<'_, PyDict> = ref_val.downcast()?;
            return Ok(RefArg::NestedRef(parse_ref_config(py, ref_dict)?));
        }
        if let Some(callable_val) = dict.get_item("__callable__")? {
            return Ok(RefArg::Callable(callable_val.unbind()));
        }
    }

    // Otherwise it's a literal value
    Ok(RefArg::Literal(obj.clone().unbind()))
}

// =============================================================================
// Utility helpers
// =============================================================================

/// Extract a string value from a dict by key.
fn get_string(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<String> {
    dict.get_item(key)?
        .ok_or_else(|| pyerr(&format!("missing '{key}'")))
        .and_then(|v| v.extract::<String>())
}

/// Create a PyValueError.
fn pyerr(msg: &str) -> PyErr {
    PyErr::new::<pyo3::exceptions::PyValueError, _>(msg.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_config_structs_exist() {
        // Compile-time test: ensure all structs are usable
        let _op = RefOp {
            name: "getitem".to_string(),
            args: vec![],
        };
        let _ref = RefConfig {
            source: "op1".to_string(),
            var: "result".to_string(),
            ops: vec![_op],
            is_output: false,
        };
        let _param = ParamConfig {
            var_name: "x".to_string(),
            ref_config: Some(_ref),
            literal: None,
            default_value: None,
            required: true,
        };
    }
}
