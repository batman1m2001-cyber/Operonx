//! Op type enum, op-bound enum, and the **serialized op configuration** types.
//!
//! Mirrors Python's `op_config.py` + the flat dict emitted by
//! `BaseOp.serialize()` / `GraphOp.serialize()`. Instead of a per-variant Rust
//! enum (which would fight serde's tag/untagged modes when upstream JSON is a
//! single flat dict), we use one flat [`OpConfig`] struct that carries every
//! possible field. Dispatch happens at runtime on the `kind` tag — matches how
//! Python writes its configs.
//!
//! # Phase 4 scope
//! - [`OpType`] / [`OpBound`] — already existed; preserved.
//! - [`OpConfig`] — new. Covers base-op metadata + the `FuncOp` flags
//!   (`func_name`, `is_async`, `is_generator`) + the `GraphOp` extras
//!   (`ops`, `edges`, `entries`, `exits`, `initial_ready_count`,
//!   `compiled_adj`, `stream_initial_ready`, `loop_config`,
//!   `max_stream_concurrent`).
//! - [`CompiledLink`] — `[dst, soft]` tuple encoding emitted by Python.
//! - [`LoopConfig`] — `until: Option<String>` + `max_iterations`. Loop vars are
//!   *not* a dedicated field — they ride on normal `inputs`. See §4b.2.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::edge_config::EdgeConfig;
use crate::core::ops::cache::CacheConfig;
use crate::core::utils::common::Param;

/// All op types supported in a workflow graph.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum OpType {
    // Data
    #[serde(rename = "data")]
    Data,

    // AI / ML
    #[serde(rename = "llm")]
    Llm,
    #[serde(rename = "embedding")]
    Embedding,
    #[serde(rename = "rerank")]
    Rerank,

    // Control flow
    #[serde(rename = "branch")]
    Branch,
    #[serde(rename = "for")]
    ForLoop,
    #[serde(rename = "while")]
    WhileLoop,
    #[serde(rename = "stream")]
    Stream,

    // Processing
    #[serde(rename = "code")]
    Code,
    #[serde(rename = "lambda")]
    Lambda,
    #[serde(rename = "parser")]
    Parser,
    #[serde(rename = "prompt")]
    Prompt,
    #[serde(rename = "doc-processor")]
    DocProcessor,

    // Database / storage
    #[serde(rename = "milvus")]
    Milvus,
    #[serde(rename = "mongo")]
    Mongo,
    #[serde(rename = "s3")]
    S3,

    // Special
    #[serde(rename = "graph")]
    Graph,
    #[serde(rename = "default")]
    Default,
    #[serde(rename = "dummy")]
    Dummy,
    #[serde(rename = "tool-executor")]
    ToolExecutor,
    #[serde(rename = "mcp")]
    Mcp,

    /// Triton Inference Server. **Not in Python's `Literal[...]` declaration**
    /// but set at runtime by [`TritonOp`](../../providers/ops/triton). The
    /// discrepancy is a known inconsistency on the Python side — Rust
    /// mirrors runtime behavior.
    #[serde(rename = "triton")]
    Triton,

    /// ONNX inference. Same Python-side inconsistency as `Triton` — set at
    /// runtime by [`OnnxOp`](../../providers/ops/onnx) despite missing from
    /// the `Literal[...]` declaration.
    #[serde(rename = "onnx")]
    Onnx,
}

impl Default for OpType {
    fn default() -> Self {
        OpType::Default
    }
}

impl OpType {
    /// Returns the Python string literal for this op type.
    pub fn as_str(&self) -> &'static str {
        match self {
            OpType::Data => "data",
            OpType::Llm => "llm",
            OpType::Embedding => "embedding",
            OpType::Rerank => "rerank",
            OpType::Branch => "branch",
            OpType::ForLoop => "for",
            OpType::WhileLoop => "while",
            OpType::Stream => "stream",
            OpType::Code => "code",
            OpType::Lambda => "lambda",
            OpType::Parser => "parser",
            OpType::Prompt => "prompt",
            OpType::DocProcessor => "doc-processor",
            OpType::Milvus => "milvus",
            OpType::Mongo => "mongo",
            OpType::S3 => "s3",
            OpType::Graph => "graph",
            OpType::Default => "default",
            OpType::Dummy => "dummy",
            OpType::ToolExecutor => "tool-executor",
            OpType::Mcp => "mcp",
            OpType::Triton => "triton",
            OpType::Onnx => "onnx",
        }
    }
}

/// Execution-bound hint — scheduler uses this to pick a dispatch strategy.
///
/// Mirrors Python's `bound: "sync" | "io" | "cpu"` field on `BaseOp`.
/// See MIGRATION_rust.md §3b for dispatch mapping.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum OpBound {
    /// Run inline on the scheduler thread. Fastest. For trivial / sync Rust ops.
    #[default]
    Sync,
    /// I/O-bound — `tokio::spawn`. For HTTP, LLM streams, disk.
    Io,
    /// CPU-bound — `rayon::spawn`. For ONNX, heavy compute. Keeps tokio workers free.
    Cpu,
}

// ── Compiled adjacency link ───────────────────────────────────────────────

/// One entry in [`OpConfig::compiled_adj`]. Python emits these as `[dst, soft]`
/// (a JSON array, not an object) — we mirror that on the wire.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CompiledLink {
    pub dst: String,
    pub soft: bool,
}

impl Serialize for CompiledLink {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeTuple;
        let mut tup = serializer.serialize_tuple(2)?;
        tup.serialize_element(&self.dst)?;
        tup.serialize_element(&self.soft)?;
        tup.end()
    }
}

impl<'de> Deserialize<'de> for CompiledLink {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let (dst, soft) = <(String, bool)>::deserialize(deserializer)?;
        Ok(CompiledLink { dst, soft })
    }
}

// ── LoopConfig ────────────────────────────────────────────────────────────

/// Loop-control block for [`GraphOp::loop`](../../ops/graph/graph_op/struct.GraphOp.html#method.loop).
///
/// Per §4b.2 the `until` field is **strings only** (callable conditions are
/// dropped at Python serialization time), and there is **no `loop_vars`
/// field** — loop state is carried through ordinary `inputs`.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct LoopConfig {
    #[serde(default)]
    pub until: Option<String>,
    #[serde(default)]
    pub max_iterations: Option<u32>,
}

// ── OpConfig — the serialized form of any op ──────────────────────────────

fn true_default() -> bool {
    true
}

fn default_concurrency() -> u32 {
    64
}

/// The runtime's view of one serialized op — flat, all-fields-present.
///
/// Matches the dict emitted by `BaseOp.serialize()` + `GraphOp.serialize()`.
/// Fields not applicable to a given `kind` stay at their defaults (`None`,
/// empty collection, etc.). Dispatch at the scheduler layer reads `kind` and
/// picks which fields to consult.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OpConfig {
    // ── Base metadata (every op) ──────────────────────────────────────────
    #[serde(rename = "type")]
    pub kind: OpType,

    pub name: String,

    #[serde(default)]
    pub full_name: String,

    #[serde(default = "true_default")]
    pub enabled: bool,

    #[serde(default)]
    pub verbose: bool,

    #[serde(default)]
    pub stream: bool,

    #[serde(default)]
    pub bound: OpBound,

    #[serde(default)]
    pub inputs: BTreeMap<String, Param>,

    #[serde(default)]
    pub outputs: BTreeMap<String, Param>,

    #[serde(default)]
    pub cache: Option<CacheConfig>,

    #[serde(default)]
    pub delay: f64,

    #[serde(default)]
    pub description: Option<String>,

    // ── FuncOp (`type = "code"`) fields ───────────────────────────────────
    #[serde(default)]
    pub func_name: Option<String>,

    #[serde(default)]
    pub is_async: bool,

    #[serde(default)]
    pub is_generator: bool,

    // ── GraphOp (`type = "graph"`) fields ─────────────────────────────────
    #[serde(default)]
    pub ops: BTreeMap<String, OpConfig>,

    #[serde(default)]
    pub edges: Vec<EdgeConfig>,

    #[serde(default)]
    pub entries: Vec<String>,

    #[serde(default)]
    pub exits: Vec<String>,

    #[serde(default)]
    pub initial_ready_count: BTreeMap<String, i32>,

    #[serde(default)]
    pub compiled_adj: BTreeMap<String, Vec<CompiledLink>>,

    #[serde(default)]
    pub stream_initial_ready: BTreeMap<String, BTreeMap<String, i32>>,

    #[serde(default)]
    pub loop_config: Option<LoopConfig>,

    #[serde(default = "default_concurrency")]
    pub max_stream_concurrent: u32,

    // ── Provider-op fields (LLMOp / EmbeddingOp / RerankOp / OnnxOp /
    //    TritonOp). Flat extensions set by `LLMOp.serialize()` etc. ────────
    /// Resource key(s) for the backend to use. String for single-model ops,
    /// list for load-balanced LLMs.
    #[serde(default)]
    pub resource: Option<Value>,

    /// Weighted load-balancing ratios (length matches `resource` when list).
    #[serde(default)]
    pub ratios: Option<Vec<f32>>,

    /// Ordered fallback chain — tried when the primary resource errors.
    #[serde(default)]
    pub fallback: Option<Vec<String>>,

    /// OpenAI Batch API mode (50% cheaper, async polling).
    #[serde(default)]
    pub batch_mode: bool,

    /// Per-LLM embedded backend configs — Python serializes full
    /// `LLMConfig`/`EmbeddingConfig`/etc dicts under this key.
    #[serde(default)]
    pub resource_config: Option<Value>,
    #[serde(default)]
    pub resource_configs: Vec<Value>,
    #[serde(default)]
    pub fallback_configs: Vec<Value>,

    /// Triton input/output tensor-name mapping.
    #[serde(default)]
    pub inputs_map: BTreeMap<String, String>,
    #[serde(default)]
    pub outputs_map: BTreeMap<String, String>,

    // ── Python-only field explicitly dropped on deserialize ───────────────
    /// `python_callable` rides on Python's internal dict but must not be
    /// deserialized into Rust. We accept it (so `deny_unknown_fields` configs
    /// round-trip) and never re-emit it.
    #[serde(default, skip_serializing, rename = "python_callable")]
    pub python_callable: Option<Value>,
}

impl Default for OpConfig {
    fn default() -> Self {
        Self {
            kind: OpType::default(),
            name: String::new(),
            full_name: String::new(),
            enabled: true,
            verbose: false,
            stream: false,
            bound: OpBound::default(),
            inputs: BTreeMap::new(),
            outputs: BTreeMap::new(),
            cache: None,
            delay: 0.0,
            description: None,
            func_name: None,
            is_async: false,
            is_generator: false,
            ops: BTreeMap::new(),
            edges: Vec::new(),
            entries: Vec::new(),
            exits: Vec::new(),
            initial_ready_count: BTreeMap::new(),
            compiled_adj: BTreeMap::new(),
            stream_initial_ready: BTreeMap::new(),
            loop_config: None,
            max_stream_concurrent: default_concurrency(),
            resource: None,
            ratios: None,
            fallback: None,
            batch_mode: false,
            resource_config: None,
            resource_configs: Vec::new(),
            fallback_configs: Vec::new(),
            inputs_map: BTreeMap::new(),
            outputs_map: BTreeMap::new(),
            python_callable: None,
        }
    }
}

impl OpConfig {
    /// Normalize `resource` into a `Vec<String>`:
    /// - `None` → `[]`
    /// - `Some(String("x"))` → `["x"]`
    /// - `Some(Array(["x","y"]))` → `["x","y"]`
    pub fn resource_keys(&self) -> Vec<String> {
        match &self.resource {
            None | Some(Value::Null) => Vec::new(),
            Some(Value::String(s)) => vec![s.clone()],
            Some(Value::Array(arr)) => arr
                .iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect(),
            _ => Vec::new(),
        }
    }

    /// `true` if this op's resource is a list (triggers load-balanced
    /// selection).
    pub fn is_multi_resource(&self) -> bool {
        matches!(&self.resource, Some(Value::Array(_)))
    }
}

impl OpConfig {
    /// `true` if this config describes a nested graph (`kind == Graph`).
    pub fn is_graph(&self) -> bool {
        matches!(self.kind, OpType::Graph)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compiled_link_roundtrips_as_tuple() {
        let link = CompiledLink {
            dst: "next".into(),
            soft: true,
        };
        let json = serde_json::to_string(&link).unwrap();
        assert_eq!(json, r#"["next",true]"#);
        let back: CompiledLink = serde_json::from_str(&json).unwrap();
        assert_eq!(back, link);
    }

    #[test]
    fn op_config_parses_graph_envelope() {
        let src = r#"{
            "type": "graph",
            "name": "main",
            "full_name": "main",
            "ops": {},
            "edges": [],
            "entries": [],
            "exits": [],
            "initial_ready_count": {},
            "compiled_adj": {},
            "stream_initial_ready": {}
        }"#;
        let cfg: OpConfig = serde_json::from_str(src).unwrap();
        assert!(cfg.is_graph());
        assert_eq!(cfg.name, "main");
        assert_eq!(cfg.max_stream_concurrent, 64);
    }

    #[test]
    fn op_config_parses_func_op() {
        let src = r#"{
            "type": "code",
            "name": "double",
            "full_name": "main.double",
            "func_name": "double",
            "is_async": false,
            "is_generator": false,
            "bound": "sync"
        }"#;
        let cfg: OpConfig = serde_json::from_str(src).unwrap();
        assert!(!cfg.is_graph());
        assert_eq!(cfg.kind, OpType::Code);
        assert_eq!(cfg.func_name.as_deref(), Some("double"));
        assert_eq!(cfg.bound, OpBound::Sync);
    }

    #[test]
    fn python_callable_is_accepted_and_dropped_on_reserialize() {
        let src = r#"{
            "type": "code",
            "name": "f",
            "full_name": "main.f",
            "python_callable": "<function f at 0x…>"
        }"#;
        let cfg: OpConfig = serde_json::from_str(src).unwrap();
        // Survives round-trip without emitting the field.
        let out = serde_json::to_string(&cfg).unwrap();
        assert!(!out.contains("python_callable"));
    }

    #[test]
    fn loop_config_only_accepts_string_until() {
        let src = r#"{"until": "count >= 5", "max_iterations": 10}"#;
        let cfg: LoopConfig = serde_json::from_str(src).unwrap();
        assert_eq!(cfg.until.as_deref(), Some("count >= 5"));
        assert_eq!(cfg.max_iterations, Some(10));

        let none_src = r#"{"until": null, "max_iterations": null}"#;
        let cfg: LoopConfig = serde_json::from_str(none_src).unwrap();
        assert!(cfg.until.is_none());
        assert!(cfg.max_iterations.is_none());
    }
}
