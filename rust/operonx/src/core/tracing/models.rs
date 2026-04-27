//! Trace data model — `TraceNode`, `TraceSummary`, `TraceData`.
//!
//! Mirrors Python [`operonx/core/tracing/models.py`](../../../../operonx/core/tracing/models.py)
//! per plan §6a (extends with `kind`, `media`, `usage`, `cost`,
//! `thinking_content`, `parent_trace_key`).
//!
//! The collector builds `TraceNode` values; tracers receive them wrapped in
//! a `TraceData` envelope (matches Python §6b.10 — nodes arrive as
//! pre-serialized dicts).

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::core::media::MediaRef;

/// One node in the trace tree.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraceNode {
    /// Stable per-node identifier — `"op_full_name:ctx"` or a synthetic key
    /// (e.g. `"$ctx:main.s0"`).
    pub trace_key: String,

    /// Pre-computed parent in the trace tree (`None` for the root). Per
    /// Python — collector resolves this so tracers don't have to.
    #[serde(default)]
    pub parent_trace_key: Option<String>,

    /// Op's full name; `None` for synthetic wrapper nodes.
    #[serde(default)]
    pub op_name: Option<String>,

    /// Short UI label — e.g. `"analyze"`, `"[0]"`, `"[iter 0]"`.
    pub display_name: String,

    /// One of `"trace" | "span" | "generation"`.
    pub node_type: String,

    /// One of `"batch" | "generator" | "stream_context" | "stream_item" |
    /// "loop_iter" | "labeled_iter" | "graph"`.
    pub kind: String,

    #[serde(default)]
    pub inputs: Map<String, Value>,

    #[serde(default)]
    pub outputs: Map<String, Value>,

    #[serde(default)]
    pub start_time: Option<String>,

    #[serde(default)]
    pub end_time: Option<String>,

    #[serde(default)]
    pub duration_ms: Option<f64>,

    #[serde(default)]
    pub metadata: Map<String, Value>,

    // ── LLM-specific (node_type == "generation") ─────────────────────────
    #[serde(default)]
    pub model: Option<String>,

    #[serde(default)]
    pub usage: Option<Value>,

    #[serde(default)]
    pub cost: Option<f64>,

    /// Anthropic-style reasoning / thinking output surfaced by `LLMOp.extras`.
    #[serde(default)]
    pub thinking_content: Option<String>,

    /// Media blobs extracted by the collector — tracers upload / discard
    /// these separately from the main JSON payload, then patch back in via
    /// `MediaRef.field_path`.
    #[serde(default)]
    pub media: Vec<MediaRef>,
}

impl Default for TraceNode {
    fn default() -> Self {
        Self {
            trace_key: String::new(),
            parent_trace_key: None,
            op_name: None,
            display_name: String::new(),
            node_type: "span".into(),
            kind: "batch".into(),
            inputs: Map::new(),
            outputs: Map::new(),
            start_time: None,
            end_time: None,
            duration_ms: None,
            metadata: Map::new(),
            model: None,
            usage: None,
            cost: None,
            thinking_content: None,
            media: Vec::new(),
        }
    }
}

/// Top-level summary emitted alongside the node tree.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TraceSummary {
    #[serde(default)]
    pub total_ops: u32,
    #[serde(default)]
    pub total_records: u32,
    #[serde(default)]
    pub total_duration_ms: f64,
    /// Number of generator ops.
    #[serde(default)]
    pub stream_count: u32,
    /// Sum of all yield counts.
    #[serde(default)]
    pub total_yields: u32,
    #[serde(default)]
    pub loop_iterations: u32,
    #[serde(default)]
    pub error_count: u32,
}

/// Payload handed to every registered tracer on workflow completion.
///
/// Matches §6b.10 — nodes are already serialized, not `TraceNode` structs.
/// Rust shape uses the typed [`TraceNode`] until the moment of flush (where
/// the tracer decides whether to serialize to JSON or dispatch directly).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TraceData {
    pub request_id: String,
    pub workflow_name: String,
    #[serde(default)]
    pub user_id: Option<String>,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub summary: TraceSummary,
    /// Full node tree (flat list — parent pointers encoded in
    /// `TraceNode::parent_trace_key`).
    #[serde(default)]
    pub nodes: Vec<TraceNode>,
}
