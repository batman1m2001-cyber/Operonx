//! Trace data models — TraceNode and TraceSummary.
//!
//! Mirrors Python's `hush.core.tracing.models`.
//! Pre-computed tree nodes with parent_trace_key already resolved,
//! so tracers do a simple parent lookup with zero heuristics.

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Pre-computed tree node for trace visualization.
///
/// The collector builds these with `parent_trace_key` already resolved.
/// Tracers (Langfuse, OTEL, etc.) do a simple parent lookup.
///
/// Kinds:
/// - `"batch"`: Normal op execution.
/// - `"generator"`: Generator summary (yield_count, total time).
/// - `"stream_context"`: Synthetic grouping span for execution slice `[N]`.
/// - `"stream_item"`: Op execution inside a stream context.
/// - `"loop_iter"`: Synthetic grouping span for loop iteration `[iter N]`.
/// - `"graph"`: Nested GraphOp container.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraceNode {
    /// Unique identifier (e.g. `"root.step:main"`, `"$ctx:root:main.s0"`).
    pub trace_key: String,
    /// Parent's trace_key (`None` for root).
    pub parent_trace_key: Option<String>,

    /// Op full_name (`None` for synthetic nodes).
    pub op_name: Option<String>,
    /// Short name for UI (e.g. `"analyze"`, `"[0]"`, `"[iter 0]"`).
    pub display_name: String,
    /// `"trace"` | `"span"` | `"generation"`.
    pub node_type: String,
    /// `"batch"` | `"generator"` | `"stream_context"` | `"stream_item"` | `"loop_iter"` | `"graph"`.
    pub kind: String,

    #[serde(default)]
    pub inputs: Value,
    #[serde(default)]
    pub outputs: Value,
    pub start_time: Option<String>,
    pub end_time: Option<String>,
    pub duration_ms: Option<f64>,
    #[serde(default)]
    pub metadata: Value,

    // LLM-specific (node_type="generation" only)
    pub model: Option<String>,
    pub usage: Option<Value>,
    pub cost: Option<f64>,
}

/// Top-level summary of a workflow execution.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraceSummary {
    pub total_ops: usize,
    pub total_records: usize,
    pub total_duration_ms: f64,
    pub stream_count: usize,
    pub total_yields: usize,
    pub loop_iterations: usize,
    pub error_count: usize,
}

impl Default for TraceSummary {
    fn default() -> Self {
        TraceSummary {
            total_ops: 0,
            total_records: 0,
            total_duration_ms: 0.0,
            stream_count: 0,
            total_yields: 0,
            loop_iterations: 0,
            error_count: 0,
        }
    }
}
