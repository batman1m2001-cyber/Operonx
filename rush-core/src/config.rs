//! Config parsing — deserialize JSON config into Rust structs.
//!
//! The config JSON is produced by `json.dumps(GraphOp.serialize(), default=str)` in Python.
//! This module parses it into typed Rust structs for GIL-free execution.
//!
//! No PyO3 types anywhere — pure Rust structs with serde_json::Value for literals.

use ahash::{AHashMap, AHashSet};
use serde_json::Value;
use smallvec::SmallVec;

use crate::error::RushError;
use rush_providers::config::{
    embedding::EmbeddingConfig,
    llm::{AzureConfig, GeminiConfig, LLMBaseFields, LLMConfig, OpenAIConfig},
    reranking::RerankingConfig,
    LLMProviderConfig, ProviderConfig,
};

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
    pub is_async: bool,
    pub enabled: bool,
    pub verbose: bool,
    /// Whether this op uses streaming mode (disabled in rust mode v1).
    pub stream: bool,
    /// Execution bound hint for scheduler dispatch.
    pub bound: OpBound,
    pub inputs: Vec<ParamConfig>,
    pub outputs: Vec<ParamConfig>,
    /// Branch-specific config (only for type == "branch").
    pub branch_config: Option<BranchConfig>,
    /// Nested graph config (for type == "graph", "for", "while", "map").
    pub inner_graph: Option<Box<GraphConfig>>,
    /// Iteration config (for type == "for", "while", "map").
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

/// Iteration op configuration (ForOp, WhileOp, MapOp).
pub struct IterationConfig {
    pub each: Vec<IterParamConfig>,
    pub broadcast: Vec<IterParamConfig>,
    pub fail_fast: bool,
    pub until: Option<String>,
    pub max_iterations: Option<usize>,
    /// Max concurrent iterations (MapOp). Defaults to CPU count.
    pub max_concurrency: Option<usize>,
}

/// A single iteration parameter (each or broadcast).
pub struct IterParamConfig {
    pub var_name: String,
    pub ref_config: Option<RefConfig>,
    pub literal: Option<Value>,
}

/// A single input/output parameter.
pub struct ParamConfig {
    pub var_name: String,
    pub ref_config: Option<RefConfig>,
    pub literal: Option<Value>,
    pub default_value: Option<Value>,
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
    Literal(Value),
    NestedRef(RefConfig),
}

// =============================================================================
// Parsing from JSON
// =============================================================================

impl GraphConfig {
    /// Parse a GraphConfig from a JSON string (output of `json.dumps(GraphOp.serialize())`).
    pub fn from_json(json_str: &str) -> Result<Self, RushError> {
        let value: Value = serde_json::from_str(json_str)?;
        Self::from_value(&value)
    }

    /// Parse a GraphConfig from a serde_json::Value.
    pub fn from_value(val: &Value) -> Result<Self, RushError> {
        let name = get_string(val, "name")?;
        let full_name = get_string(val, "full_name")?;

        // Parse ops
        let ops_val = val.get("ops").ok_or_else(|| err("missing 'ops'"))?;
        let ops_obj = ops_val.as_object().ok_or_else(|| err("'ops' is not an object"))?;

        let mut ops = AHashMap::with_capacity(ops_obj.len());
        for (op_name, op_val) in ops_obj {
            let op_config = OpConfig::from_value(op_val)?;
            ops.insert(op_name.clone(), op_config);
        }

        // Parse entries
        let entries = val
            .get("entries")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|v| v.as_str().map(String::from)).collect())
            .unwrap_or_default();

        // Parse initial_ready_count
        let mut initial_ready_count = AHashMap::new();
        if let Some(rc_obj) = val.get("initial_ready_count").and_then(|v| v.as_object()) {
            for (k, v) in rc_obj {
                if let Some(n) = v.as_i64() {
                    initial_ready_count.insert(k.clone(), n as i32);
                }
            }
        }

        // Parse compiled_adj: {"op_name": [["target", false], ...]}
        let mut compiled_adj = AHashMap::new();
        if let Some(adj_obj) = val.get("compiled_adj").and_then(|v| v.as_object()) {
            for (op_name, succs_val) in adj_obj {
                let mut entries_vec: SmallVec<[AdjEntry; 4]> = SmallVec::new();
                if let Some(succs) = succs_val.as_array() {
                    for item in succs {
                        if let Some(pair) = item.as_array() {
                            let target = pair.first().and_then(|v| v.as_str()).unwrap_or("").to_string();
                            let is_soft = pair.get(1).and_then(|v| v.as_bool()).unwrap_or(false);
                            entries_vec.push(AdjEntry { target, is_soft });
                        }
                    }
                }
                compiled_adj.insert(op_name.clone(), entries_vec);
            }
        }

        // Parse has_soft_preds
        let has_soft_preds = val
            .get("has_soft_preds")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|v| v.as_str().map(String::from)).collect())
            .unwrap_or_default();

        // Parse graph-level inputs/outputs
        let inputs = parse_params(val, "inputs")?;
        let outputs = parse_params(val, "outputs")?;

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
    /// Parse an OpConfig from a JSON value (output of `BaseOp.serialize()`).
    fn from_value(val: &Value) -> Result<Self, RushError> {
        let op_type = get_string(val, "type")?;
        let full_name = get_string(val, "full_name")?;
        let name = get_string(val, "name")?;

        let rust_op = get_opt_string(val, "rust_op");
        let is_async = get_bool(val, "is_async", false);
        let enabled = get_bool(val, "enabled", true);
        let verbose = get_bool(val, "verbose", false);
        let stream = get_bool(val, "stream", false);
        let bound = match get_opt_string(val, "bound").as_deref() {
            Some("io") => OpBound::Io,
            _ => OpBound::Cpu,
        };

        let inputs = parse_params(val, "inputs")?;
        let outputs = parse_params(val, "outputs")?;

        // Parse branch config (only for type == "branch")
        let branch_config = if op_type == "branch" {
            Some(parse_branch_config(val)?)
        } else {
            None
        };

        // Parse inner graph (for "graph", "for", "while", "map" types)
        // These ops' serialized dicts ARE GraphConfig — they have ops, entries, etc.
        let inner_graph = if matches!(op_type.as_str(), "graph" | "for" | "while" | "map") {
            Some(Box::new(GraphConfig::from_value(val)?))
        } else {
            None
        };

        // Parse iteration config (for "for", "while", "map" types)
        let iteration_config = if matches!(op_type.as_str(), "for" | "while" | "map") {
            Some(parse_iteration_config(val, &op_type)?)
        } else {
            None
        };

        // Parse provider config (for "llm", "embedding", "rerank" types)
        let provider_config = parse_provider_config_json(&op_type, val)?;

        let op = OpConfig {
            op_type,
            full_name,
            name,
            rust_op,
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
        };

        // Build-time validation: reject ops that cannot run in Rust mode
        op.validate()?;

        Ok(op)
    }

    /// Validate that this op can execute in GIL-free Rust mode.
    /// Returns an error for unsupported ops, giving the user a clear message
    /// at config parse time rather than a cryptic runtime failure.
    fn validate(&self) -> Result<(), RushError> {
        // Reject AIterOp (streaming iteration)
        if self.op_type == "stream" {
            return Err(RushError::UnsupportedOp(format!(
                "Op '{}': AIterOp (stream) is not supported in rust mode. Use MapOp instead.",
                self.full_name
            )));
        }

        // Reject streaming LLM ops
        if self.stream {
            return Err(RushError::UnsupportedOp(format!(
                "Op '{}': streaming (stream=true) is not supported in rust mode.",
                self.full_name
            )));
        }

        // Structural ops (graph, for, while, map, branch) are always valid
        if matches!(
            self.op_type.as_str(),
            "graph" | "for" | "while" | "map" | "branch"
        ) {
            return Ok(());
        }

        // Provider ops with config are valid
        if self.provider_config.is_some() {
            return Ok(());
        }

        // Native transform ops (prompt, parser) are valid
        if rush_providers::ops::is_native_transform_op(&self.op_type) {
            return Ok(());
        }

        // Plugin ops (rust_op with "path::func" format) are valid
        if let Some(ref spec) = self.rust_op {
            if spec.contains("::") {
                return Ok(());
            }
        }

        // Everything else has no Rust implementation
        Err(RushError::UnsupportedOp(format!(
            "Op '{}' (type={}) has no Rust implementation. \
             Use @op(rust='./crate::func') to provide one.",
            self.full_name, self.op_type
        )))
    }
}

// =============================================================================
// Branch config parsing
// =============================================================================

fn parse_branch_config(val: &Value) -> Result<BranchConfig, RushError> {
    let cases_arr = val
        .get("cases")
        .and_then(|v| v.as_array())
        .ok_or_else(|| err("branch op missing 'cases'"))?;

    let mut cases = Vec::with_capacity(cases_arr.len());
    for item in cases_arr {
        let cond_val = item
            .get("condition")
            .ok_or_else(|| err("branch case missing 'condition'"))?;
        let condition = parse_ref_config(cond_val)?;
        let target = get_string(item, "target")?;
        cases.push(BranchCase { condition, target });
    }

    let default = get_opt_string(val, "default");

    Ok(BranchConfig { cases, default })
}

// =============================================================================
// Iteration config parsing
// =============================================================================

fn parse_iteration_config(val: &Value, op_type: &str) -> Result<IterationConfig, RushError> {
    let each = parse_iter_params(val, "each")?;
    let broadcast = parse_iter_params(val, "broadcast")?;

    let fail_fast = if matches!(op_type, "for" | "map") {
        get_bool(val, "fail_fast", false)
    } else {
        false
    };

    let until = if op_type == "while" {
        get_opt_string(val, "until")
    } else {
        None
    };

    let max_iterations = if op_type == "while" {
        val.get("max_iterations").and_then(|v| v.as_u64()).map(|n| n as usize)
    } else {
        None
    };

    let max_concurrency = if op_type == "map" {
        val.get("max_concurrency").and_then(|v| v.as_u64()).map(|n| n as usize)
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
    })
}

/// Parse an "each" or "broadcast" dict into Vec<IterParamConfig>.
/// Format: {"var_name": {"ref": {...}|null, "literal": ...|null}}
fn parse_iter_params(parent: &Value, key: &str) -> Result<Vec<IterParamConfig>, RushError> {
    let obj = match parent.get(key).and_then(|v| v.as_object()) {
        Some(o) => o,
        None => return Ok(Vec::new()),
    };

    let mut result = Vec::with_capacity(obj.len());
    for (var_name, entry) in obj {
        let ref_config = entry
            .get("ref")
            .filter(|v| !v.is_null())
            .map(|v| parse_ref_config(v))
            .transpose()?;

        let literal = entry.get("literal").filter(|v| !v.is_null()).cloned();

        result.push(IterParamConfig {
            var_name: var_name.clone(),
            ref_config,
            literal,
        });
    }

    Ok(result)
}

// =============================================================================
// Param / Ref parsing helpers
// =============================================================================

/// Parse "inputs" or "outputs" dict from a config value.
/// Format: {"var_name": {"ref": {...}|null, "literal": ...|null, "default": ...|null, "required": bool}}
fn parse_params(parent: &Value, key: &str) -> Result<Vec<ParamConfig>, RushError> {
    let obj = match parent.get(key).and_then(|v| v.as_object()) {
        Some(o) => o,
        None => return Ok(Vec::new()),
    };

    let mut result = Vec::with_capacity(obj.len());
    for (var_name, entry) in obj {
        let ref_config = entry
            .get("ref")
            .filter(|v| !v.is_null())
            .map(|v| parse_ref_config(v))
            .transpose()?;

        let literal = entry.get("literal").filter(|v| !v.is_null()).cloned();
        let default_value = entry.get("default").filter(|v| !v.is_null()).cloned();
        let required = entry.get("required").and_then(|v| v.as_bool()).unwrap_or(false);

        result.push(ParamConfig {
            var_name: var_name.clone(),
            ref_config,
            literal,
            default_value,
            required,
        });
    }

    Ok(result)
}

/// Parse a RefConfig from a JSON value (output of `Ref.serialize()`).
/// Format: {"source": str, "var": str, "ops": [[name, [args...]], ...], "is_output": bool}
fn parse_ref_config(val: &Value) -> Result<RefConfig, RushError> {
    let source = get_string(val, "source")?;
    let var = get_string(val, "var")?;
    let is_output = val.get("is_output").and_then(|v| v.as_bool()).unwrap_or(false);

    // Parse ops: [[name, [args...]], ...]
    let ops_arr = val
        .get("ops")
        .and_then(|v| v.as_array())
        .ok_or_else(|| err("missing 'ops' in ref config"))?;

    let mut ops = Vec::with_capacity(ops_arr.len());
    for item in ops_arr {
        let pair = item.as_array().ok_or_else(|| err("ref op must be an array"))?;
        let op_name = pair
            .first()
            .and_then(|v| v.as_str())
            .ok_or_else(|| err("ref op name must be a string"))?
            .to_string();

        let args_arr = pair
            .get(1)
            .and_then(|v| v.as_array())
            .ok_or_else(|| err("ref op args must be an array"))?;

        let mut args = Vec::with_capacity(args_arr.len());
        for arg in args_arr {
            args.push(parse_ref_arg(arg)?);
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

/// Parse a single RefArg from a JSON value.
/// Can be: {"__ref__": {...}} for nested Ref, {"__callable__": ...} for callable (error), or a literal.
fn parse_ref_arg(val: &Value) -> Result<RefArg, RushError> {
    if let Some(obj) = val.as_object() {
        if let Some(ref_val) = obj.get("__ref__") {
            return Ok(RefArg::NestedRef(parse_ref_config(ref_val)?));
        }
        if obj.contains_key("__callable__") {
            return Err(RushError::UnsupportedOp(
                "Ref.apply() with callable is not supported in rust mode. Use @op(rust=...) instead."
                    .into(),
            ));
        }
    }

    // Otherwise it's a literal value
    Ok(RefArg::Literal(val.clone()))
}

// =============================================================================
// Provider config parsing (from JSON)
// =============================================================================

/// Parse a ProviderConfig from an op's JSON value.
/// Constructs rush_providers config structs directly from JSON fields.
fn parse_provider_config_json(
    op_type: &str,
    val: &Value,
) -> Result<Option<ProviderConfig>, RushError> {
    match op_type {
        "llm" => parse_llm_provider_json(val),
        "embedding" => parse_embedding_provider_json(val),
        "rerank" => parse_reranking_provider_json(val),
        _ => Ok(None),
    }
}

fn parse_llm_provider_json(val: &Value) -> Result<Option<ProviderConfig>, RushError> {
    let configs_arr = match val.get("resource_configs").and_then(|v| v.as_array()) {
        Some(a) if !a.is_empty() => a,
        _ => return Ok(None),
    };

    let mut configs = Vec::with_capacity(configs_arr.len());
    for cfg_val in configs_arr {
        configs.push(parse_llm_config_json(cfg_val)?);
    }

    let resources = parse_json_string_or_list(val, "resource");
    let ratios = parse_json_float_list(val, "ratios");
    let fallback = parse_json_string_list(val, "fallback");
    let batch_mode = get_bool(val, "batch_mode", false);

    let mut fallback_configs = Vec::new();
    if let Some(fb_arr) = val.get("fallback_configs").and_then(|v| v.as_array()) {
        for cfg_val in fb_arr {
            if let Ok(cfg) = parse_llm_config_json(cfg_val) {
                fallback_configs.push(cfg);
            }
        }
    }

    Ok(Some(ProviderConfig::LLM(LLMProviderConfig {
        configs,
        resources,
        ratios,
        fallback_configs,
        fallback,
        batch_mode,
    })))
}

fn parse_llm_config_json(val: &Value) -> Result<LLMConfig, RushError> {
    let api_type = get_string(val, "api_type")?;

    let base = LLMBaseFields {
        proxy: get_opt_string(val, "proxy"),
        cost_per_input_token: val.get("cost_per_input_token").and_then(|v| v.as_f64()),
        cost_per_output_token: val.get("cost_per_output_token").and_then(|v| v.as_f64()),
    };

    match api_type.as_str() {
        "openai" | "vllm" => Ok(LLMConfig::OpenAI(OpenAIConfig {
            base,
            api_type: api_type.clone(),
            api_key: get_string(val, "api_key")?,
            base_url: get_string(val, "base_url")?,
            model: get_string(val, "model")?,
            batch_size: val.get("batch_size").and_then(|v| v.as_u64()).unwrap_or(50000) as usize,
            batch_flush_interval: val.get("batch_flush_interval").and_then(|v| v.as_f64()).unwrap_or(60.0),
            batch_poll_interval: val.get("batch_poll_interval").and_then(|v| v.as_f64()).unwrap_or(30.0),
            batch_timeout: val.get("batch_timeout").and_then(|v| v.as_f64()).unwrap_or(86400.0),
        })),
        "azure" => Ok(LLMConfig::Azure(AzureConfig {
            base,
            api_key: get_string(val, "api_key")?,
            api_version: get_string(val, "api_version")?,
            azure_endpoint: get_string(val, "azure_endpoint")?,
            model: get_string(val, "model")?,
        })),
        "gemini" => Ok(LLMConfig::Gemini(GeminiConfig {
            base,
            project_id: get_string(val, "project_id")?,
            private_key_id: get_string(val, "private_key_id")?,
            private_key: get_string(val, "private_key")?,
            client_email: get_string(val, "client_email")?,
            client_id: get_string(val, "client_id")?,
            auth_uri: get_opt_string(val, "auth_uri")
                .unwrap_or_else(|| "https://accounts.google.com/o/oauth2/auth".to_string()),
            token_uri: get_opt_string(val, "token_uri")
                .unwrap_or_else(|| "https://oauth2.googleapis.com/token".to_string()),
            auth_provider_x509_cert_url: get_opt_string(val, "auth_provider_x509_cert_url")
                .unwrap_or_else(|| "https://www.googleapis.com/oauth2/v1/certs".to_string()),
            client_x509_cert_url: get_string(val, "client_x509_cert_url")?,
            universe_domain: get_opt_string(val, "universe_domain")
                .unwrap_or_else(|| "googleapis.com".to_string()),
            location: get_opt_string(val, "location")
                .unwrap_or_else(|| "us-central1".to_string()),
            model: get_opt_string(val, "model")
                .unwrap_or_else(|| "gemini-2.0-flash-001".to_string()),
        })),
        _ => Err(RushError::ConfigError(format!(
            "Unsupported LLM api_type: '{api_type}'"
        ))),
    }
}

fn parse_embedding_provider_json(val: &Value) -> Result<Option<ProviderConfig>, RushError> {
    let cfg_val = match val.get("resource_config").filter(|v| !v.is_null()) {
        Some(v) => v,
        None => return Ok(None),
    };

    Ok(Some(ProviderConfig::Embedding(EmbeddingConfig {
        api_type: get_string(cfg_val, "api_type")?,
        api_key: get_opt_string(cfg_val, "api_key"),
        base_url: get_opt_string(cfg_val, "base_url"),
        model: get_opt_string(cfg_val, "model"),
        embed_batch_size: cfg_val.get("embed_batch_size").and_then(|v| v.as_u64()).map(|n| n as usize),
        dimensions: cfg_val.get("dimensions").and_then(|v| v.as_u64()).map(|n| n as usize),
    })))
}

fn parse_reranking_provider_json(val: &Value) -> Result<Option<ProviderConfig>, RushError> {
    let cfg_val = match val.get("resource_config").filter(|v| !v.is_null()) {
        Some(v) => v,
        None => return Ok(None),
    };

    Ok(Some(ProviderConfig::Reranking(RerankingConfig {
        api_type: get_string(cfg_val, "api_type")?,
        api_key: get_opt_string(cfg_val, "api_key"),
        api_version: get_opt_string(cfg_val, "api_version"),
        base_url: get_opt_string(cfg_val, "base_url"),
        model: get_opt_string(cfg_val, "model"),
    })))
}

// =============================================================================
// JSON parsing helpers
// =============================================================================

/// Extract a required string from a JSON value.
fn get_string(val: &Value, key: &str) -> Result<String, RushError> {
    val.get(key)
        .and_then(|v| v.as_str())
        .map(String::from)
        .ok_or_else(|| RushError::ConfigError(format!("missing or non-string '{key}'")))
}

/// Extract an optional string from a JSON value.
fn get_opt_string(val: &Value, key: &str) -> Option<String> {
    val.get(key)
        .filter(|v| !v.is_null())
        .and_then(|v| v.as_str())
        .map(String::from)
}

/// Extract a bool with a default from a JSON value.
fn get_bool(val: &Value, key: &str, default: bool) -> bool {
    val.get(key).and_then(|v| v.as_bool()).unwrap_or(default)
}

/// Parse a string or list of strings from a JSON value.
fn parse_json_string_or_list(val: &Value, key: &str) -> Vec<String> {
    match val.get(key) {
        Some(Value::Array(arr)) => arr.iter().filter_map(|v| v.as_str().map(String::from)).collect(),
        Some(Value::String(s)) => vec![s.clone()],
        _ => Vec::new(),
    }
}

/// Parse a list of floats from a JSON value.
fn parse_json_float_list(val: &Value, key: &str) -> Vec<f64> {
    val.get(key)
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|v| v.as_f64()).collect())
        .unwrap_or_default()
}

/// Parse a list of strings from a JSON value.
fn parse_json_string_list(val: &Value, key: &str) -> Vec<String> {
    val.get(key)
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|v| v.as_str().map(String::from)).collect())
        .unwrap_or_default()
}

/// Create a RushError::ConfigError.
fn err(msg: &str) -> RushError {
    RushError::ConfigError(msg.to_string())
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

    #[test]
    fn test_parse_ref_arg_literal() {
        let val = serde_json::json!(42);
        let arg = parse_ref_arg(&val).unwrap();
        assert!(matches!(arg, RefArg::Literal(_)));
    }

    #[test]
    fn test_parse_ref_arg_nested_ref() {
        let val = serde_json::json!({
            "__ref__": {
                "source": "op1",
                "var": "x",
                "ops": [],
                "is_output": false
            }
        });
        let arg = parse_ref_arg(&val).unwrap();
        assert!(matches!(arg, RefArg::NestedRef(_)));
    }

    #[test]
    fn test_parse_ref_arg_callable_rejected() {
        let val = serde_json::json!({"__callable__": "some_func"});
        let result = parse_ref_arg(&val);
        assert!(result.is_err());
    }
}
