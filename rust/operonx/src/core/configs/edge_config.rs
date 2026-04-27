//! Edge config for workflow graphs.
//!
//! Mirrors Python `operonx/core/configs/edge_config.py`.

use serde::{Deserialize, Serialize};

/// Edge kind between ops — matches Python's `EdgeType = Literal[...]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum EdgeType {
    Normal,
    Lookback,
    Condition,
}

impl Default for EdgeType {
    fn default() -> Self {
        EdgeType::Normal
    }
}

/// Edge between two ops in a workflow graph.
///
/// # Rust naming notes
///
/// Python attribute names `from_node` / `to_node` conflict with Rust reserved `from`;
/// JSON serialization in `GraphOp.serialize()` uses `"from"` and `"to"` keys.
/// Rust fields use `from_op` / `to_op` with `#[serde(rename = ...)]` to preserve
/// JSON compatibility.
///
/// Python's `type:` field would collide with Rust's `type` keyword, so the Rust
/// field is `kind` (see MIGRATION_rust.md §3a.7).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EdgeConfig {
    #[serde(rename = "from")]
    pub from_op: String,

    #[serde(rename = "to")]
    pub to_op: String,

    #[serde(rename = "type", default)]
    pub kind: EdgeType,

    #[serde(default)]
    pub soft: bool,
}
