//! TraceCollector — post-processing step for extracting trace data.
//!
//! Mirrors Python's `hush.core.tracing.collector.TraceCollector`.
//! Completely separated from ops — ops don't know about tracing.
//! After workflow execution, the collector walks the compiled graph for static
//! metadata and reads dynamic execution data from EngineState.
//!
//! Each (op, context) execution becomes a TraceNode. Streaming ops are grouped
//! under synthetic [N] context nodes for hierarchical visualization.

use std::collections::{BTreeMap, HashMap, HashSet};

use serde_json::{json, Value};

use crate::config::{GraphConfig, BaseOpConfig};
use crate::states::state::EngineState;
use crate::tracing::models::{TraceNode, TraceSummary};

/// Static per-op info precomputed from GraphConfig.
struct OpInfo {
    parent_graph_name: Option<String>,
    is_root: bool,
    is_graph_op: bool,
    is_gen: bool,
    display_name: String,
    contain_generation: bool,
    /// Input var names (non-$ prefixed) from BaseOpConfig.
    input_vars: Vec<String>,
}

/// Extracts trace data from GraphConfig (static) + EngineState (dynamic).
///
/// Graph metadata is precomputed once in `new()`. Each `collect()` call
/// does per-run state reading, context grouping, and tree building.
pub struct TraceCollector {
    graph_name: String,
    /// full_name → OpInfo
    op_info: HashMap<String, OpInfo>,
    /// full_name → topo rank (for sibling sorting)
    topo_ranks: HashMap<String, usize>,
}

impl TraceCollector {
    /// Precompute all graph-derived metadata.
    pub fn new(config: &GraphConfig) -> Self {
        let mut op_info = HashMap::new();
        let mut topo_ranks = HashMap::new();

        // Build op_info recursively
        Self::build_op_info(config, None, true, &mut op_info);

        // Build topo ranks from compiled_adj
        Self::build_topo_ranks(config, &mut topo_ranks);

        TraceCollector {
            graph_name: config.name.clone(),
            
            op_info,
            topo_ranks,
        }
    }

    /// Extract trace data as a pre-computed tree of TraceNodes.
    pub fn collect(&self, state: &EngineState, skip_pending: bool) -> Value {
        // 1. Collect executed (op_name, context) pairs from state
        let mut executed_pairs: HashSet<(String, String)> = HashSet::new();
        for op_name in self.op_info.keys() {
            for (ctx, _start) in state.iter_executed(op_name) {
                executed_pairs.insert((op_name.clone(), ctx));
            }
        }

        // 2. Discover stream contexts from state ($stream_ctx markers)
        let stream_contexts = self.discover_stream_contexts(state);

        // 3. Scan state → TraceNodes with parents resolved
        let (mut node_lookup, node_meta) =
            self.scan_nodes(state, &executed_pairs, &stream_contexts);

        // 4. Add synthetic context groups ([0], [1], ...)
        self.add_context_groups(&mut node_lookup, &node_meta);

        // 5. Skip pending generators and empty context groups
        if skip_pending {
            Self::remove_pending(&mut node_lookup);
        }
        // 6. Sort by DAG edge order → final list
        let children_map = build_children(&node_lookup);
        let result = self.sort_by_edges(&node_lookup, &children_map);
        // 7. Summary + payload
        let summary = self.build_summary(&result);
        let nodes_json: Vec<Value> = result
            .iter()
            .map(|n| serde_json::to_value(n).unwrap_or(Value::Null))
            .collect();
        let summary_json = serde_json::to_value(&summary).unwrap_or(Value::Null);

        json!({
            "request_id": state.request_id().unwrap_or_default(),
            "workflow_name": self.graph_name,
            "tags": state.tags(),
            "nodes": nodes_json,
            "summary": summary_json,
        })
    }

    // =========================================================================
    // Graph Walking (init-time)
    // =========================================================================

    fn build_op_info(
        config: &GraphConfig,
        parent_name: Option<&str>,
        is_root: bool,
        result: &mut HashMap<String, OpInfo>,
    ) {
        // Add the graph itself as a node
        let display_name = if is_root {
            config.name.clone()
        } else {
            config
                .full_name
                .rsplit('.')
                .next()
                .unwrap_or(&config.name)
                .to_string()
        };

        result.insert(
            config.full_name.clone(),
            OpInfo {
                parent_graph_name: parent_name.map(String::from),
                is_root,
                is_graph_op: !is_root,
                is_gen: false,
                display_name,
                contain_generation: false,
                input_vars: config
                    .inputs
                    .iter()
                    .map(|p| p.var_name.clone())
                    .collect(),
            },
        );

        // Add child ops
        for (_, op) in &config.ops {
            Self::build_op_info_for_op(op, &config.full_name, result);
        }
    }

    fn build_op_info_for_op(
        op: &BaseOpConfig,
        parent_graph_name: &str,
        result: &mut HashMap<String, OpInfo>,
    ) {
        let short_name = op
            .full_name
            .rsplit('.')
            .next()
            .unwrap_or(&op.name)
            .to_string();

        result.insert(
            op.full_name.clone(),
            OpInfo {
                parent_graph_name: Some(parent_graph_name.to_string()),
                is_root: false,
                is_graph_op: op.op_type == "graph",
                is_gen: op.is_generator,
                display_name: short_name,
                contain_generation: op.contain_generation,
                input_vars: op
                    .inputs
                    .iter()
                    .filter(|p| !p.var_name.starts_with('$'))
                    .map(|p| p.var_name.clone())
                    .collect(),
            },
        );

        // Recurse into nested graphs
        if let Some(ref inner) = op.inner_graph {
            Self::build_op_info(inner, Some(&op.full_name), false, result);
        }
    }

    fn build_topo_ranks(config: &GraphConfig, ranks: &mut HashMap<String, usize>) {
        // Build topo order from initial_ready_count + compiled_adj
        // Simple BFS-based topo sort
        let mut in_degree: HashMap<&str, i32> = HashMap::new();
        for (name, count) in &config.initial_ready_count {
            in_degree.insert(name.as_str(), *count);
        }

        let mut queue: Vec<&str> = Vec::new();
        for entry in &config.entries {
            if *in_degree.get(entry.as_str()).unwrap_or(&0) == 0 {
                queue.push(entry.as_str());
            }
        }

        // Also check ops not in initial_ready_count (ready_count=0)
        for name in config.ops.keys() {
            if !in_degree.contains_key(name.as_str()) {
                if config.entries.contains(name) {
                    // already handled
                } else {
                    in_degree.insert(name.as_str(), 0);
                }
            }
        }

        let mut rank = 0;
        while !queue.is_empty() {
            let mut next_queue = Vec::new();
            for &name in &queue {
                // Map short name to full_name for this graph's ops
                if let Some(op) = config.ops.get(name) {
                    ranks.insert(op.full_name.clone(), rank);
                }
            }
            for &name in &queue {
                if let Some(succs) = config.compiled_adj.get(name) {
                    for succ in succs.iter() {
                        if let Some(deg) = in_degree.get_mut(succ.target.as_str()) {
                            *deg -= 1;
                            if *deg <= 0 {
                                next_queue.push(succ.target.as_str());
                            }
                        }
                    }
                }
            }
            queue = next_queue;
            rank += 1;
        }

        // Recurse into nested graphs
        for op in config.ops.values() {
            if let Some(ref inner) = op.inner_graph {
                Self::build_topo_ranks(inner, ranks);
            }
        }
    }

    // =========================================================================
    // Discover stream contexts from state
    // =========================================================================

    /// Discover stream contexts by looking at contexts that contain `[N]` segments.
    /// In Python, this is read from `graph.stream_contexts`. In Rust, we derive
    /// it from state by scanning for contexts with stream index segments.
    fn discover_stream_contexts(&self, state: &EngineState) -> HashMap<String, Vec<Vec<String>>> {
        // Map: parent_graph_full_name → list of stream context segment lists
        let mut result: HashMap<String, Vec<Vec<String>>> = HashMap::new();

        for op_name in self.op_info.keys() {
            for (ctx, _) in state.iter_executed(op_name) {
                let segments = ctx_to_segments(&ctx);
                // Check if any segment is a stream index [N]
                for (i, seg) in segments.iter().enumerate() {
                    if is_stream_segment(seg) {
                        // The stream context is segments[..=i]
                        let stream_ctx = segments[..=i].to_vec();
                        // The parent graph for this op
                        if let Some(info) = self.op_info.get(op_name) {
                            if let Some(ref parent) = info.parent_graph_name {
                                let entry = result.entry(parent.clone()).or_default();
                                if !entry.contains(&stream_ctx) {
                                    entry.push(stream_ctx);
                                }
                            }
                        }
                        break; // Only care about first stream segment for discovery
                    }
                }
            }
        }

        result
    }

    // =========================================================================
    // Scan state → TraceNodes
    // =========================================================================

    fn scan_nodes(
        &self,
        state: &EngineState,
        executed_pairs: &HashSet<(String, String)>,
        stream_contexts: &HashMap<String, Vec<Vec<String>>>,
    ) -> (BTreeMap<String, TraceNode>, HashMap<String, NodeMeta>) {
        let mut node_lookup: BTreeMap<String, TraceNode> = BTreeMap::new();
        let mut node_meta: HashMap<String, NodeMeta> = HashMap::new();

        for (op_name, info) in &self.op_info {
            let parent_name = info.parent_graph_name.as_deref();

            // Get stream contexts for this op's parent graph
            let parent_stream_ctxs = parent_name
                .and_then(|p| stream_contexts.get(p))
                .cloned()
                .unwrap_or_default();

            for (ctx_str, _start_time) in state.iter_executed(op_name) {
                let ctx_segments = ctx_to_segments(&ctx_str);

                // Determine kind
                let kind = determine_kind(&ctx_segments, info.is_gen, info.is_graph_op);

                // Read inputs/outputs
                let inputs = self.read_io(state, op_name, &ctx_str, &info.input_vars);
                let outputs_vars: Vec<String> = state
                    .get_vars_for_op(op_name, &ctx_str)
                    .into_iter()
                    .map(|(k, _)| k)
                    .collect();
                let mut outputs = self.read_io(state, op_name, &ctx_str, &outputs_vars);

                // Aggregate outputs for generators
                if info.is_gen && outputs.as_object().map_or(false, |o| o.values().all(|v| v.is_null())) {
                    let mut agg: HashMap<String, Vec<Value>> = HashMap::new();
                    for v in &outputs_vars {
                        agg.insert(v.clone(), Vec::new());
                    }
                    for sc_segs in &parent_stream_ctxs {
                        if sc_segs.len() == ctx_segments.len() + 1
                            && sc_segs[..ctx_segments.len()] == ctx_segments[..]
                        {
                            let sc_str = segments_to_ctx(sc_segs);
                            for v in &outputs_vars {
                                if let Some(val) = state.get(op_name, v, &sc_str) {
                                    if !val.is_null() {
                                        agg.entry(v.clone()).or_default().push((*val).clone());
                                    }
                                }
                            }
                        }
                    }
                    if agg.values().any(|v| !v.is_empty()) {
                        let mut agg_obj = serde_json::Map::new();
                        for (k, v) in agg {
                            agg_obj.insert(k, Value::Array(v));
                        }
                        outputs = Value::Object(agg_obj);
                    }
                }

                // Timing
                let start_time = state.get_meta(op_name, "$start_time", &ctx_str);
                let end_time = state.get_meta(op_name, "$end_time", &ctx_str);
                let duration_ms = state
                    .get_meta(op_name, "$duration_ms", &ctx_str)
                    .and_then(|v| v.as_f64());

                // LLM-specific
                let model = if info.contain_generation {
                    outputs.get("model_used").and_then(|v| v.as_str()).map(String::from)
                } else {
                    None
                };
                let usage = if info.contain_generation {
                    outputs.get("tokens_used").cloned()
                } else {
                    None
                };
                let cost = state
                    .get_meta(op_name, "cost_usd", &ctx_str)
                    .and_then(|v| v.as_f64());

                // Trace key
                let trace_key = format!("{}:{}", op_name, ctx_str);

                // node_type
                let node_type = if info.is_root {
                    "trace"
                } else if info.contain_generation {
                    "generation"
                } else {
                    "span"
                };

                // Metadata
                let mut metadata = serde_json::Map::new();
                if kind != "batch" {
                    metadata.insert("kind".into(), json!(kind));
                }
                if kind == "generator" {
                    let yield_count =
                        count_yields(&ctx_segments, &parent_stream_ctxs);
                    metadata.insert("yield_count".into(), json!(yield_count));
                    if yield_count == 0 {
                        metadata.insert("status".into(), json!("pending"));
                    }
                }

                // Parent resolution
                let (parent_trace_key, matched_parent_ctx) = if info.is_root {
                    (None, Vec::new())
                } else {
                    resolve_parent(parent_name, &ctx_segments, executed_pairs)
                };

                let node = TraceNode {
                    trace_key: trace_key.clone(),
                    parent_trace_key,
                    op_name: Some(op_name.clone()),
                    display_name: info.display_name.clone(),
                    node_type: node_type.to_string(),
                    kind: kind.to_string(),
                    inputs,
                    outputs,
                    start_time: format_time(start_time),
                    end_time: format_time(end_time),
                    duration_ms,
                    metadata: Value::Object(metadata),
                    model,
                    usage,
                    cost,
                };

                node_meta.insert(
                    trace_key.clone(),
                    NodeMeta {
                        ctx: ctx_segments,
                        matched_parent_ctx,
                        parent_graph_name: parent_name.map(String::from),
                    },
                );
                node_lookup.insert(trace_key, node);
            }
        }

        (node_lookup, node_meta)
    }

    fn read_io(
        &self,
        state: &EngineState,
        op_name: &str,
        context: &str,
        vars: &[String],
    ) -> Value {
        let mut obj = serde_json::Map::new();
        for var in vars {
            let val = state
                .get(op_name, var, context)
                .map(|v| (*v).clone())
                .unwrap_or(Value::Null);
            obj.insert(var.clone(), val);
        }
        Value::Object(obj)
    }

    // =========================================================================
    // Context grouping
    // =========================================================================

    fn add_context_groups(
        &self,
        node_lookup: &mut BTreeMap<String, TraceNode>,
        node_meta: &HashMap<String, NodeMeta>,
    ) {
        // Build generator context map: (parent_graph, ctx_str) → trace_key
        let mut gen_ctx_map: HashMap<(String, String), String> = HashMap::new();
        for (trace_key, meta) in node_meta {
            if let Some(node) = node_lookup.get(trace_key) {
                if node.kind == "generator" {
                    if let Some(ref parent) = meta.parent_graph_name {
                        let ctx_str = segments_to_ctx(&meta.ctx);
                        gen_ctx_map.insert((parent.clone(), ctx_str), trace_key.clone());
                    }
                }
            }
        }

        let mut synthetics: BTreeMap<String, TraceNode> = BTreeMap::new();
        // Collect re-parenting operations
        let mut reparent: Vec<(String, String)> = Vec::new();

        for (trace_key, meta) in node_meta {
            let node = match node_lookup.get(trace_key) {
                Some(n) => n,
                None => continue,
            };
            if node.node_type == "trace" {
                continue;
            }

            let ctx = &meta.ctx;
            let matched = &meta.matched_parent_ctx;
            let parent_graph_name = match &meta.parent_graph_name {
                Some(p) => p,
                None => continue,
            };

            if ctx.is_empty() || matched.is_empty() {
                continue;
            }
            if ctx.len() <= matched.len() {
                continue;
            }

            // Relative segments beyond parent graph's context
            let relative = &ctx[matched.len()..];
            if relative.is_empty() {
                continue;
            }

            // Only group pure stream contexts
            if !relative.iter().all(|s| is_stream_segment(s)) {
                continue;
            }

            // Build parent graph's trace_key
            let parent_ctx_str = segments_to_ctx(matched);
            let graph_trace_key = if parent_ctx_str.is_empty() {
                parent_graph_name.clone()
            } else {
                format!("{}:{}", parent_graph_name, parent_ctx_str)
            };

            // Create synthetic nodes for each stream segment level
            let mut deepest_synthetic = None;
            for i in 0..relative.len() {
                let full_ctx_segs: Vec<String> =
                    matched.iter().chain(&relative[..=i]).cloned().collect();
                let ctx_str = segments_to_ctx(&full_ctx_segs);
                let synthetic_key = format!("$ctx:{}:{}", parent_graph_name, ctx_str);

                if !synthetics.contains_key(&synthetic_key)
                    && !node_lookup.contains_key(&synthetic_key)
                {
                    // Find spawning generator
                    let gen_ctx_segs: Vec<String> =
                        matched.iter().chain(&relative[..i]).cloned().collect();
                    let gen_ctx_str = segments_to_ctx(&gen_ctx_segs);
                    let spawner_key =
                        gen_ctx_map.get(&(parent_graph_name.clone(), gen_ctx_str));
                    let parent_for_synthetic = spawner_key
                        .cloned()
                        .unwrap_or_else(|| graph_trace_key.clone());

                    synthetics.insert(
                        synthetic_key.clone(),
                        TraceNode {
                            trace_key: synthetic_key.clone(),
                            parent_trace_key: Some(parent_for_synthetic),
                            op_name: None,
                            display_name: relative[i].clone(),
                            node_type: "span".to_string(),
                            kind: "stream_context".to_string(),
                            inputs: Value::Null,
                            outputs: Value::Null,
                            start_time: None,
                            end_time: None,
                            duration_ms: None,
                            metadata: Value::Null,
                            model: None,
                            usage: None,
                            cost: None,
                        },
                    );
                }

                deepest_synthetic = Some(synthetic_key);
            }

            // Re-parent to deepest synthetic
            if let Some(ref syn_key) = deepest_synthetic {
                reparent.push((trace_key.clone(), syn_key.clone()));
            }
        }

        // Apply re-parenting
        for (key, new_parent) in reparent {
            if let Some(node) = node_lookup.get_mut(&key) {
                node.parent_trace_key = Some(new_parent);
            }
        }

        // Set timing from children on synthetics
        let all_nodes: Vec<(String, Option<String>, Option<String>)> = node_lookup
            .iter()
            .map(|(_, n)| {
                (
                    n.parent_trace_key.clone().unwrap_or_default(),
                    n.start_time.clone(),
                    n.end_time.clone(),
                )
            })
            .collect();

        for (key, syn_node) in &mut synthetics {
            let mut starts: Vec<&str> = Vec::new();
            let mut ends: Vec<&str> = Vec::new();
            for (parent, st, et) in &all_nodes {
                if parent == key {
                    if let Some(ref s) = st {
                        starts.push(s);
                    }
                    if let Some(ref e) = et {
                        ends.push(e);
                    }
                }
            }
            if let Some(&min_start) = starts.iter().min() {
                syn_node.start_time = Some(min_start.to_string());
            }
            if let Some(&max_end) = ends.iter().max() {
                syn_node.end_time = Some(max_end.to_string());
            }
        }

        node_lookup.extend(synthetics);
    }

    fn remove_pending(node_lookup: &mut BTreeMap<String, TraceNode>) {
        // 1. Remove pending generators
        let pending_keys: Vec<String> = node_lookup
            .iter()
            .filter(|(_, n)| {
                n.metadata
                    .get("status")
                    .and_then(|v| v.as_str())
                    == Some("pending")
            })
            .map(|(k, _)| k.clone())
            .collect();
        for k in pending_keys {
            node_lookup.remove(&k);
        }

        // 2. Cascade-remove empty synthetic context nodes
        loop {
            let parent_keys: HashSet<String> = node_lookup
                .values()
                .filter_map(|n| n.parent_trace_key.clone())
                .collect();
            let empty: Vec<String> = node_lookup
                .iter()
                .filter(|(k, n)| n.kind == "stream_context" && !parent_keys.contains(*k))
                .map(|(k, _)| k.clone())
                .collect();
            if empty.is_empty() {
                break;
            }
            for k in empty {
                node_lookup.remove(&k);
            }
        }
    }

    // =========================================================================
    // Sort by DAG edge order
    // =========================================================================

    fn sort_by_edges(
        &self,
        node_lookup: &BTreeMap<String, TraceNode>,
        children_map: &HashMap<Option<String>, Vec<String>>,
    ) -> Vec<TraceNode> {
        let ranks = &self.topo_ranks;

        let sort_key = |k: &String| -> (usize, usize, String) {
            let n = &node_lookup[k];
            if n.kind == "stream_context" {
                (999998, extract_stream_index(&n.display_name), k.clone())
            } else if let Some(ref name) = n.op_name {
                (*ranks.get(name).unwrap_or(&999999), 0, k.clone())
            } else {
                (999999, 0, k.clone())
            }
        };

        // DFS tree walk with sorting
        let roots = children_map
            .get(&None)
            .cloned()
            .unwrap_or_default();
        let mut result = Vec::new();
        tree_walk_sorted(&roots, children_map, &sort_key, node_lookup, &mut result);
        result
    }

    // =========================================================================
    // Summary
    // =========================================================================

    fn build_summary(&self, nodes: &[TraceNode]) -> TraceSummary {
        let mut total_duration = 0.0;
        let mut total_yields = 0;
        let mut loop_iterations = 0;
        let mut stream_count = 0;

        for n in nodes {
            if let Some(d) = n.duration_ms {
                total_duration += d;
            }
            if n.kind == "generator" {
                stream_count += 1;
                total_yields += n
                    .metadata
                    .get("yield_count")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0) as usize;
            }
            if n.kind == "loop_iter" {
                loop_iterations += 1;
            }
        }

        let real_count = nodes.iter().filter(|n| n.op_name.is_some()).count();
        TraceSummary {
            total_ops: self.op_info.len(),
            total_records: real_count,
            total_duration_ms: (total_duration * 100.0).round() / 100.0,
            stream_count,
            total_yields,
            loop_iterations,
            error_count: 0,
        }
    }
}

// =============================================================================
// Helper structs
// =============================================================================

struct NodeMeta {
    ctx: Vec<String>,
    matched_parent_ctx: Vec<String>,
    parent_graph_name: Option<String>,
}

// =============================================================================
// Free functions (context parsing, helpers)
// =============================================================================

/// Parse a dot-separated context string into segments.
/// `"main"` → `["main"]`, `"main.[0].[1]"` → `["main", "[0]", "[1]"]`
fn ctx_to_segments(ctx: &str) -> Vec<String> {
    if ctx.is_empty() {
        return vec![];
    }
    ctx.split('.').map(String::from).collect()
}

/// Join segments back into a dot-separated context string.
fn segments_to_ctx(segs: &[String]) -> String {
    segs.join(".")
}

fn is_stream_segment(seg: &str) -> bool {
    seg.starts_with('[') && seg.ends_with(']')
}

fn is_loop_segment(seg: &str) -> bool {
    seg.starts_with("loop_")
}

fn extract_stream_index(display_name: &str) -> usize {
    display_name
        .trim_start_matches('[')
        .trim_end_matches(']')
        .parse::<usize>()
        .unwrap_or(999999)
}

fn format_time(val: Option<Value>) -> Option<String> {
    match val {
        Some(Value::String(s)) => Some(s.replace("+00:00", "Z")),
        _ => None,
    }
}

fn determine_kind(ctx_segments: &[String], is_gen: bool, is_graph_op: bool) -> &'static str {
    if is_gen {
        return "generator";
    }
    if is_graph_op {
        return "graph";
    }
    if let Some(last) = ctx_segments.last() {
        if is_loop_segment(last) {
            return "loop_iter";
        }
    }
    "batch"
}

fn count_yields(gen_ctx: &[String], stream_contexts: &[Vec<String>]) -> usize {
    stream_contexts
        .iter()
        .filter(|sc| sc.len() == gen_ctx.len() + 1 && sc[..gen_ctx.len()] == gen_ctx[..])
        .count()
}

/// Resolve parent trace_key by walking up context hierarchy.
fn resolve_parent(
    parent_name: Option<&str>,
    ctx: &[String],
    executed_pairs: &HashSet<(String, String)>,
) -> (Option<String>, Vec<String>) {
    let parent = match parent_name {
        Some(p) => p,
        None => return (None, vec![]),
    };
    if ctx.is_empty() {
        return (Some(parent.to_string()), vec![]);
    }

    let ctx_str = segments_to_ctx(ctx);
    if executed_pairs.contains(&(parent.to_string(), ctx_str.clone())) {
        return (Some(format!("{}:{}", parent, ctx_str)), ctx.to_vec());
    }

    // Walk up
    let mut test = ctx.to_vec();
    while !test.is_empty() {
        test.pop();
        let test_str = segments_to_ctx(&test);
        if executed_pairs.contains(&(parent.to_string(), test_str.clone())) {
            return (Some(format!("{}:{}", parent, test_str)), test);
        }
    }

    (Some(parent.to_string()), vec![])
}

/// Build children map: parent_trace_key → list of child trace_keys.
fn build_children(
    node_lookup: &BTreeMap<String, TraceNode>,
) -> HashMap<Option<String>, Vec<String>> {
    let mut children: HashMap<Option<String>, Vec<String>> = HashMap::new();
    for (key, node) in node_lookup {
        children
            .entry(node.parent_trace_key.clone())
            .or_default()
            .push(key.clone());
    }
    children
}

/// DFS tree walk with sibling sorting.
fn tree_walk_sorted<F>(
    roots: &[String],
    children_map: &HashMap<Option<String>, Vec<String>>,
    sort_key: &F,
    node_lookup: &BTreeMap<String, TraceNode>,
    result: &mut Vec<TraceNode>,
) where
    F: Fn(&String) -> (usize, usize, String),
{
    let mut sorted_roots = roots.to_vec();
    sorted_roots.sort_by_key(sort_key);

    for key in sorted_roots {
        if let Some(node) = node_lookup.get(&key) {
            result.push(node.clone());
        }
        if let Some(children) = children_map.get(&Some(key.clone())) {
            tree_walk_sorted(children, children_map, sort_key, node_lookup, result);
        }
    }
}
