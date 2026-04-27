//! [`TraceCollector`] — build a `TraceData` tree from the executed workflow.
//!
//! Mirrors Python [`operonx/core/tracing/collector.py`](../../../../operonx/core/tracing/collector.py).
//! Per plan §4b.8, stream items must be grouped under synthetic
//! `stream_context` wrappers with pre-computed `parent_trace_key`.
//!
//! # Phase 7 scope
//! Minimal frame-based collector: builds a flat node list from the
//! [`FrameEvent`] stream the engine already captures, plus a root `"trace"`
//! node for the workflow. Graph-walking + op-metadata extraction (full
//! `inputs` / `outputs` per op, op-level `kind="batch"` vs generator
//! detection) lands in Phase 7b when the scheduler exposes its
//! `RuntimeState` to the collector.

use std::collections::{HashMap, HashSet};

use serde_json::{Map, Value};

use super::models::{TraceData, TraceNode, TraceSummary};
use crate::core::engine::FrameEvent;
use crate::core::states::cell::ContextId;

/// Per-execution collector. Construct with the workflow metadata; call
/// [`collect_from_frames`](TraceCollector::collect_from_frames) after
/// execution to materialize a [`TraceData`].
#[derive(Debug, Clone)]
pub struct TraceCollector {
    pub workflow_name: String,
    pub request_id: String,
    pub user_id: Option<String>,
    pub session_id: Option<String>,
    pub tags: Vec<String>,
}

impl TraceCollector {
    pub fn new(workflow_name: impl Into<String>, request_id: impl Into<String>) -> Self {
        Self {
            workflow_name: workflow_name.into(),
            request_id: request_id.into(),
            user_id: None,
            session_id: None,
            tags: Vec::new(),
        }
    }

    pub fn with_user(mut self, user_id: Option<String>) -> Self {
        self.user_id = user_id;
        self
    }

    pub fn with_session(mut self, session_id: Option<String>) -> Self {
        self.session_id = session_id;
        self
    }

    pub fn with_tags(mut self, tags: Vec<String>) -> Self {
        self.tags = tags;
        self
    }

    /// Build a `TraceData` from the frames captured by the engine.
    ///
    /// Shape of the emitted tree:
    /// - One **root** node with `node_type = "trace"`, `kind = "graph"`,
    ///   `trace_key = "__root__:{request_id}"`.
    /// - One **stream_context** wrapper per non-root context seen (e.g.
    ///   `("main", "[0]")`) with `parent_trace_key = root`.
    /// - One **span** node per `(op_name, ctx)` — child of the matching
    ///   stream_context (if non-root) or the root.
    pub fn collect_from_frames(&self, frames: &[FrameEvent]) -> TraceData {
        let root_key = format!("__root__:{}", self.request_id);
        let mut nodes: Vec<TraceNode> = Vec::with_capacity(frames.len() + 1);

        // Root trace node.
        nodes.push(TraceNode {
            trace_key: root_key.clone(),
            parent_trace_key: None,
            op_name: None,
            display_name: self.workflow_name.clone(),
            node_type: "trace".into(),
            kind: "graph".into(),
            ..Default::default()
        });

        // Synthesize stream_context wrappers — one per non-root context.
        let mut seen_ctx: HashSet<ContextId> = HashSet::new();
        let mut ctx_order: Vec<ContextId> = Vec::new();
        for f in frames {
            if f.context.len() > 1 && seen_ctx.insert(f.context.clone()) {
                ctx_order.push(f.context.clone());
            }
        }
        let ctx_keys: HashMap<ContextId, String> = ctx_order
            .iter()
            .enumerate()
            .map(|(i, ctx)| (ctx.clone(), format!("$ctx:{}:{}", self.workflow_name, i)))
            .collect();

        for (i, ctx) in ctx_order.iter().enumerate() {
            let wrapper_key = format!("$ctx:{}:{}", self.workflow_name, i);
            // Display tail — e.g. `[0]` for `("main", "[0]")`.
            let tail = ctx
                .last()
                .cloned()
                .unwrap_or_else(|| format!("[{}]", i));
            let parent_key = resolve_parent_ctx_key(ctx, &ctx_keys).unwrap_or_else(|| root_key.clone());
            nodes.push(TraceNode {
                trace_key: wrapper_key,
                parent_trace_key: Some(parent_key),
                op_name: None,
                display_name: tail,
                node_type: "span".into(),
                kind: "stream_context".into(),
                ..Default::default()
            });
        }

        // Per-frame span nodes.
        let mut counts: HashMap<(String, ContextId), usize> = HashMap::new();
        let mut error_count: u32 = 0;
        for f in frames {
            let count = counts
                .entry((f.op.clone(), f.context.clone()))
                .and_modify(|c| *c += 1)
                .or_insert(0);
            let trace_key = format!(
                "{}:{}:{}",
                f.op,
                ctx_tail(&f.context),
                count
            );
            let parent_key = if f.context.len() > 1 {
                ctx_keys
                    .get(&f.context)
                    .cloned()
                    .unwrap_or_else(|| root_key.clone())
            } else {
                root_key.clone()
            };

            if f.data.contains_key("error") {
                error_count += 1;
            }

            let mut outputs = Map::new();
            for (k, v) in &f.data {
                outputs.insert(k.clone(), v.clone());
            }

            nodes.push(TraceNode {
                trace_key,
                parent_trace_key: Some(parent_key),
                op_name: Some(f.op.clone()),
                display_name: f.op.rsplit_once('.').map(|(_, s)| s).unwrap_or(&f.op).to_string(),
                node_type: "span".into(),
                kind: if f.context.len() > 1 { "stream_item".into() } else { "batch".into() },
                inputs: Map::new(),
                outputs,
                metadata: Map::new(),
                ..Default::default()
            });
        }

        let summary = TraceSummary {
            total_ops: nodes
                .iter()
                .filter(|n| n.op_name.is_some())
                .count() as u32,
            total_records: frames.len() as u32,
            error_count,
            ..Default::default()
        };

        TraceData {
            request_id: self.request_id.clone(),
            workflow_name: self.workflow_name.clone(),
            user_id: self.user_id.clone(),
            session_id: self.session_id.clone(),
            tags: self.tags.clone(),
            summary,
            nodes,
        }
    }
}

fn ctx_tail(ctx: &ContextId) -> String {
    ctx.last().cloned().unwrap_or_else(|| "main".into())
}

/// Walk up the context tuple by dropping the last element and look up the
/// resulting prefix in `ctx_keys` — that's the parent stream_context for a
/// nested context.
fn resolve_parent_ctx_key(
    ctx: &ContextId,
    ctx_keys: &HashMap<ContextId, String>,
) -> Option<String> {
    let mut parent = ctx.clone();
    if parent.is_empty() {
        return None;
    }
    parent.pop();
    if parent.len() <= 1 {
        return None;
    }
    ctx_keys.get(&parent).cloned()
}

/// Optional unused hint suppression for `Value` import when features evolve.
#[allow(dead_code)]
fn _value_type_check(_: Value) {}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn frame(op: &str, data: Value) -> FrameEvent {
        FrameEvent {
            op: op.to_string(),
            context: crate::core::states::cell::default_context(),
            data: match data {
                Value::Object(m) => m,
                other => {
                    let mut m = Map::new();
                    m.insert("result".into(), other);
                    m
                }
            },
        }
    }

    #[test]
    fn collects_root_and_flat_spans() {
        let c = TraceCollector::new("main", "req-1");
        let frames = vec![frame("main.a", json!({"x": 1})), frame("main.b", json!({"y": 2}))];
        let td = c.collect_from_frames(&frames);
        assert_eq!(td.request_id, "req-1");
        // root + two spans.
        assert_eq!(td.nodes.len(), 3);
        assert_eq!(td.nodes[0].kind, "graph");
        assert_eq!(td.nodes[0].node_type, "trace");
        assert_eq!(td.nodes[1].op_name.as_deref(), Some("main.a"));
        assert_eq!(td.nodes[1].parent_trace_key.as_deref(), Some(td.nodes[0].trace_key.as_str()));
        assert_eq!(td.summary.total_records, 2);
    }

    #[test]
    fn stream_context_wraps_per_non_root_context() {
        let c = TraceCollector::new("main", "req-2");
        let mut stream_ctx = crate::core::states::cell::default_context();
        stream_ctx.push("[0]".into());
        let frames = vec![FrameEvent {
            op: "main.inner".into(),
            context: stream_ctx,
            data: {
                let mut m = Map::new();
                m.insert("v".into(), json!(42));
                m
            },
        }];
        let td = c.collect_from_frames(&frames);
        // Expect root + stream_context wrapper + stream_item span.
        assert_eq!(td.nodes.len(), 3);
        let wrapper = td.nodes.iter().find(|n| n.kind == "stream_context").unwrap();
        assert_eq!(wrapper.display_name, "[0]");
        let span = td.nodes.iter().find(|n| n.kind == "stream_item").unwrap();
        assert_eq!(span.parent_trace_key.as_ref(), Some(&wrapper.trace_key));
    }
}
