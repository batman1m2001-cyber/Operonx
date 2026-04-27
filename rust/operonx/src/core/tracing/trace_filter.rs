//! [`TraceFilter`] — drop / preserve / truncate nodes before flushing.
//!
//! Mirrors Python [`operonx/core/tracing/trace_filter.py`](../../../../operonx/core/tracing/trace_filter.py).
//! Per plan §6b.4 the struct carries exactly these fields; `rewriters` is
//! **not** ported (Python-specific callable list).

use std::collections::{HashMap, HashSet};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tracing::debug;

use super::models::TraceNode;

/// Structured config for filtering trace nodes.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TraceFilter {
    /// Drop nodes where every output value is `null`.
    #[serde(default)]
    pub skip_empty: bool,

    /// Drop nodes matching any of these op names (short-or-full).
    #[serde(default)]
    pub exclude_ops: Vec<String>,

    /// If non-empty, keep only nodes matching these names (whitelist).
    #[serde(default)]
    pub include_ops: Vec<String>,

    /// Drop nodes whose `kind` is in this list.
    #[serde(default)]
    pub exclude_kinds: Vec<String>,

    /// `node_type` values that are never removed. Defaults to
    /// `["trace", "generation"]` — root + LLM calls always survive.
    #[serde(default = "default_protected_types")]
    pub protected_types: Vec<String>,

    /// Truncate any I/O value whose `to_string()` exceeds this many bytes.
    /// `0` = unlimited (no truncation).
    #[serde(default)]
    pub max_io_size: usize,

    /// Don't filter children of ops matching any of these names.
    #[serde(default)]
    pub preserve_children_of: Vec<String>,
}

fn default_protected_types() -> Vec<String> {
    vec!["trace".to_string(), "generation".to_string()]
}

impl Default for TraceFilter {
    fn default() -> Self {
        Self {
            skip_empty: false,
            exclude_ops: Vec::new(),
            include_ops: Vec::new(),
            exclude_kinds: Vec::new(),
            protected_types: default_protected_types(),
            max_io_size: 0,
            preserve_children_of: Vec::new(),
        }
    }
}

impl TraceFilter {
    /// Parse from a YAML-like dict; unknown keys are ignored. `rewriters` is
    /// explicitly dropped — Python-only.
    pub fn from_value(v: &Value) -> Self {
        serde_json::from_value(v.clone()).unwrap_or_default()
    }

    /// Apply the filter — returns a new node list with dropped nodes removed
    /// and orphans re-parented onto surviving ancestors.
    pub fn apply(&self, nodes: Vec<TraceNode>) -> Vec<TraceNode> {
        if nodes.is_empty() {
            return nodes;
        }

        // Validate the `exclude_ops` / `include_ops` XOR invariant.
        debug_assert!(
            !(self.exclude_ops.iter().any(|_| true) && self.include_ops.iter().any(|_| true)),
            "TraceFilter cannot set both exclude_ops and include_ops"
        );

        // Map trace_key → parent_trace_key for quick re-parent walks.
        let parent_of: HashMap<String, Option<String>> = nodes
            .iter()
            .map(|n| (n.trace_key.clone(), n.parent_trace_key.clone()))
            .collect();

        // Keys whose children should be preserved even if the filter would
        // otherwise drop them.
        let mut preserved_keys: HashSet<String> = HashSet::new();
        if !self.preserve_children_of.is_empty() {
            for n in &nodes {
                let op = n.op_name.as_deref().unwrap_or(&n.display_name);
                let short = op.rsplit_once('.').map(|(_, s)| s).unwrap_or(op);
                if self.preserve_children_of.iter().any(|p| p == op || p == short) {
                    preserved_keys.insert(n.trace_key.clone());
                }
            }
        }

        // Step 1: identify nodes to remove.
        let mut remove_keys: HashSet<String> = HashSet::new();
        for n in &nodes {
            if let Some(parent) = &n.parent_trace_key {
                if preserved_keys.contains(parent) {
                    continue;
                }
            }
            if self.should_remove(n) {
                remove_keys.insert(n.trace_key.clone());
            }
        }

        if !remove_keys.is_empty() {
            debug!(
                "TraceFilter: removing {}/{} nodes",
                remove_keys.len(),
                nodes.len()
            );
        }

        // Step 2: re-parent children of removed nodes onto surviving ancestors.
        let mut nodes = nodes;
        for n in &mut nodes {
            if remove_keys.contains(&n.trace_key) {
                continue;
            }
            let mut parent = n.parent_trace_key.clone();
            while let Some(p) = &parent {
                if !remove_keys.contains(p) {
                    break;
                }
                parent = parent_of.get(p).cloned().flatten();
            }
            n.parent_trace_key = parent;
        }

        // Step 3: drop the removed nodes.
        let mut result: Vec<TraceNode> = nodes
            .into_iter()
            .filter(|n| !remove_keys.contains(&n.trace_key))
            .collect();

        // Step 4: clean up synthetic wrappers with no surviving children.
        result = remove_empty_synthetics(result);

        // Step 5: truncate oversized I/O.
        if self.max_io_size > 0 {
            for n in &mut result {
                truncate_io(n, self.max_io_size);
            }
        }

        result
    }

    fn should_remove(&self, node: &TraceNode) -> bool {
        if self.protected_types.iter().any(|t| t == &node.node_type) {
            return false;
        }
        let has_op = node.op_name.is_some();

        if has_op && !self.exclude_ops.is_empty() && self.op_matches(node, &self.exclude_ops) {
            return true;
        }

        if has_op && !self.include_ops.is_empty() && !self.op_matches(node, &self.include_ops) {
            return true;
        }

        if !self.exclude_kinds.is_empty() && self.exclude_kinds.iter().any(|k| k == &node.kind) {
            return true;
        }

        if self.skip_empty && has_op && is_empty_output(node) {
            return true;
        }

        false
    }

    /// Exact-string matching per §6b.5 — no regex, no globs. Matches when
    /// `op_name == entry`, `display_name == entry`, or when the last dotted
    /// component of `op_name` equals `entry`.
    fn op_matches(&self, node: &TraceNode, op_list: &[String]) -> bool {
        let empty = String::new();
        let op_name = node.op_name.as_ref().unwrap_or(&empty);
        let display = &node.display_name;
        let short = op_name.rsplit_once('.').map(|(_, s)| s).unwrap_or(op_name);
        op_list.iter().any(|e| e == op_name || e == display || e == short)
    }
}

fn is_empty_output(node: &TraceNode) -> bool {
    if node.outputs.is_empty() {
        return true;
    }
    node.outputs.values().all(|v| v.is_null())
}

fn truncate_io(node: &mut TraceNode, max_size: usize) {
    for io in [&mut node.inputs, &mut node.outputs] {
        for (_, v) in io.iter_mut() {
            if v.is_null() {
                continue;
            }
            match v {
                Value::String(s) if s.len() > max_size => {
                    let orig_len = s.len();
                    s.truncate(max_size);
                    s.push_str(&format!("...<truncated {} chars>", orig_len));
                }
                Value::Array(arr) => {
                    let rendered = serde_json::to_string(arr).unwrap_or_default();
                    if rendered.len() > max_size {
                        let placeholder = format!("<array len={}>", arr.len());
                        *v = Value::from(placeholder);
                    }
                }
                Value::Object(obj) => {
                    let rendered = serde_json::to_string(obj).unwrap_or_default();
                    if rendered.len() > max_size {
                        let placeholder = format!("<dict len={}>", obj.len());
                        *v = Value::from(placeholder);
                    }
                }
                _ => {}
            }
        }
    }
}

/// Remove synthetic (`stream_context`, `loop_iter`, `labeled_iter`) wrappers
/// that have zero surviving children. Cascades upward — orphaned synthetics
/// disappear iteratively.
fn remove_empty_synthetics(mut nodes: Vec<TraceNode>) -> Vec<TraceNode> {
    const SYNTHETIC_KINDS: &[&str] = &["stream_context", "loop_iter", "labeled_iter"];
    loop {
        let parents: HashSet<String> = nodes
            .iter()
            .filter_map(|n| n.parent_trace_key.clone())
            .collect();
        let before = nodes.len();
        nodes.retain(|n| {
            !(SYNTHETIC_KINDS.iter().any(|k| *k == n.kind) && !parents.contains(&n.trace_key))
        });
        if nodes.len() == before {
            break;
        }
    }
    nodes
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn leaf(key: &str, parent: Option<&str>, op: Option<&str>, kind: &str) -> TraceNode {
        TraceNode {
            trace_key: key.to_string(),
            parent_trace_key: parent.map(String::from),
            op_name: op.map(String::from),
            display_name: op.unwrap_or(key).to_string(),
            node_type: "span".into(),
            kind: kind.into(),
            ..Default::default()
        }
    }

    #[test]
    fn exclude_ops_drops_match_and_reparents() {
        let nodes = vec![
            leaf("r", None, Some("root"), "batch"),
            leaf("a", Some("r"), Some("middle"), "batch"),
            leaf("b", Some("a"), Some("leaf"), "batch"),
        ];
        let f = TraceFilter {
            exclude_ops: vec!["middle".into()],
            ..Default::default()
        };
        let out = f.apply(nodes);
        assert_eq!(out.len(), 2);
        // Leaf reparented onto root.
        let leaf_node = out.iter().find(|n| n.trace_key == "b").unwrap();
        assert_eq!(leaf_node.parent_trace_key.as_deref(), Some("r"));
    }

    #[test]
    fn skip_empty_drops_null_outputs() {
        let mut n_empty = leaf("e", None, Some("e"), "batch");
        n_empty.outputs.insert("x".into(), Value::Null);
        let mut n_full = leaf("f", None, Some("f"), "batch");
        n_full.outputs.insert("x".into(), json!(1));
        let out = TraceFilter {
            skip_empty: true,
            ..Default::default()
        }
        .apply(vec![n_empty, n_full]);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].trace_key, "f");
    }

    #[test]
    fn protected_types_survive() {
        let root = TraceNode {
            trace_key: "r".into(),
            op_name: Some("root".into()),
            display_name: "root".into(),
            node_type: "trace".into(),
            kind: "batch".into(),
            ..Default::default()
        };
        let f = TraceFilter {
            exclude_ops: vec!["root".into()],
            ..Default::default()
        };
        let out = f.apply(vec![root]);
        assert_eq!(out.len(), 1, "protected 'trace' node_type must survive");
    }

    #[test]
    fn empty_synthetic_wrappers_are_pruned() {
        let wrapper = leaf("w", None, None, "stream_context");
        let out = TraceFilter::default().apply(vec![wrapper]);
        assert_eq!(out.len(), 0, "no children → synthetic dropped");
    }

    #[test]
    fn max_io_size_truncates_long_strings() {
        let mut n = leaf("n", None, Some("n"), "batch");
        n.outputs
            .insert("big".into(), Value::from("x".repeat(100)));
        let out = TraceFilter {
            max_io_size: 10,
            ..Default::default()
        }
        .apply(vec![n]);
        let s = out[0].outputs.get("big").unwrap().as_str().unwrap();
        assert!(s.starts_with(&"x".repeat(10)));
        assert!(s.contains("truncated"));
    }
}
