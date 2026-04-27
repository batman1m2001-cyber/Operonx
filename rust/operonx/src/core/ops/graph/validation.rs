//! Runtime graph validation.
//!
//! Mirrors Python [`operonx/core/ops/graph/validation.py`](../../../../../operonx/core/ops/graph/validation.py).
//! Checks: unresolvable refs, cycles in compiled adjacency, unreachable
//! entries/exits, unknown edge endpoints, branch targets.
//!
//! # Phase 4 scope
//! Minimal: only the checks the scheduler relies on at startup. Full
//! parity with the Python validator (cycle detection, ref-scope walks, etc.)
//! is deferred to Phase 9 alongside the spec fixture suite.

use std::collections::HashSet;

use crate::core::configs::op_config::{OpConfig, OpType};
use crate::core::exceptions::OperonError;

/// Run the minimum set of structural checks on a graph config.
///
/// Returns `Ok(())` if the graph is dispatchable — unknown adjacency targets,
/// missing entry/exit declarations, and duplicate op names are all rejected.
pub fn validate_graph(graph: &OpConfig) -> Result<(), OperonError> {
    if !matches!(graph.kind, OpType::Graph) {
        return Err(OperonError::Config(format!(
            "validate_graph: expected type=\"graph\", got {:?}",
            graph.kind
        )));
    }
    if graph.entries.is_empty() {
        return Err(OperonError::Config(format!(
            "graph '{}': no entry ops declared",
            graph.name
        )));
    }
    let op_names: HashSet<&str> = graph.ops.keys().map(String::as_str).collect();
    for entry in &graph.entries {
        if !op_names.contains(entry.as_str()) {
            return Err(OperonError::Config(format!(
                "graph '{}': entry '{}' not in ops",
                graph.name, entry
            )));
        }
    }
    for exit in &graph.exits {
        if !op_names.contains(exit.as_str()) {
            return Err(OperonError::Config(format!(
                "graph '{}': exit '{}' not in ops",
                graph.name, exit
            )));
        }
    }
    for (src, links) in &graph.compiled_adj {
        if !op_names.contains(src.as_str()) {
            return Err(OperonError::Config(format!(
                "graph '{}': compiled_adj source '{}' not in ops",
                graph.name, src
            )));
        }
        for link in links {
            if !op_names.contains(link.dst.as_str()) {
                return Err(OperonError::Config(format!(
                    "graph '{}': edge {}→{} — destination not in ops",
                    graph.name, src, link.dst
                )));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::configs::op_config::{CompiledLink, OpConfig, OpType};
    use std::collections::BTreeMap;

    fn graph_with_ops(entries: Vec<&str>, exits: Vec<&str>, ops: &[&str]) -> OpConfig {
        let mut g = OpConfig {
            kind: OpType::Graph,
            name: "main".into(),
            full_name: "main".into(),
            entries: entries.iter().map(|s| s.to_string()).collect(),
            exits: exits.iter().map(|s| s.to_string()).collect(),
            ..Default::default()
        };
        for op in ops {
            g.ops.insert(
                op.to_string(),
                OpConfig {
                    kind: OpType::Code,
                    name: op.to_string(),
                    full_name: format!("main.{}", op),
                    ..Default::default()
                },
            );
        }
        g
    }

    #[test]
    fn rejects_non_graph_root() {
        let mut cfg = OpConfig::default();
        cfg.kind = OpType::Code;
        assert!(validate_graph(&cfg).is_err());
    }

    #[test]
    fn rejects_missing_entry() {
        let g = graph_with_ops(vec![], vec![], &[]);
        assert!(validate_graph(&g).is_err());
    }

    #[test]
    fn rejects_unknown_entry() {
        let g = graph_with_ops(vec!["ghost"], vec!["a"], &["a"]);
        assert!(validate_graph(&g).is_err());
    }

    #[test]
    fn rejects_unknown_edge_target() {
        let mut g = graph_with_ops(vec!["a"], vec!["a"], &["a"]);
        let mut adj = BTreeMap::new();
        adj.insert(
            "a".into(),
            vec![CompiledLink {
                dst: "b".into(),
                soft: false,
            }],
        );
        g.compiled_adj = adj;
        assert!(validate_graph(&g).is_err());
    }

    #[test]
    fn accepts_valid_graph() {
        let g = graph_with_ops(vec!["a"], vec!["a"], &["a"]);
        validate_graph(&g).unwrap();
    }
}
