use rusqlite::Connection;
use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::api::models::{TraceNode, TraceSummary};

/// List trace summaries grouped by request_id, with pagination and optional time filter.
pub fn list_traces(
    conn: &Connection,
    limit: i64,
    offset: i64,
    time_filter: Option<f64>,
) -> rusqlite::Result<(Vec<TraceSummary>, i64)> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();

    let cutoff = time_filter.map(|tf| now - tf);

    // Count total distinct request_ids
    let total: i64 = if let Some(cutoff) = cutoff {
        conn.query_row(
            "SELECT COUNT(DISTINCT request_id) FROM traces WHERE created_at >= ?1",
            [cutoff],
            |row| row.get(0),
        )?
    } else {
        conn.query_row(
            "SELECT COUNT(DISTINCT request_id) FROM traces",
            [],
            |row| row.get(0),
        )?
    };

    // Query trace summaries
    let sql = format!(
        "SELECT
            t.request_id,
            t.workflow_name,
            MIN(t.start_time) as start_time,
            MAX(CASE WHEN t.parent_name IS NULL THEN t.duration_ms ELSE 0 END) as total_duration_ms,
            SUM(CASE WHEN t.contain_generation = 1 THEN t.prompt_tokens ELSE 0 END) as prompt_tokens,
            SUM(CASE WHEN t.contain_generation = 1 THEN t.completion_tokens ELSE 0 END) as completion_tokens,
            SUM(CASE WHEN t.contain_generation = 1 THEN t.total_tokens ELSE 0 END) as total_tokens,
            SUM(CASE WHEN t.contain_generation = 1 THEN t.cost_usd ELSE 0 END) as total_cost,
            COUNT(*) as node_count,
            MAX(t.tags) as tags,
            MAX(CASE WHEN t.parent_name IS NULL THEN t.input ELSE NULL END) as root_input,
            MAX(CASE WHEN t.parent_name IS NULL THEN t.output ELSE NULL END) as root_output
        FROM traces t
        {}
        GROUP BY t.request_id
        ORDER BY MIN(t.created_at) DESC
        LIMIT ?1 OFFSET ?2",
        if cutoff.is_some() {
            "WHERE t.created_at >= ?3"
        } else {
            ""
        }
    );

    let mut stmt = conn.prepare(&sql)?;

    let rows = if let Some(cutoff) = cutoff {
        stmt.query_map(rusqlite::params![limit, offset, cutoff], |row| {
            map_trace_summary(row)
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?
    } else {
        stmt.query_map(rusqlite::params![limit, offset], |row| {
            map_trace_summary(row)
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?
    };

    Ok((rows, total))
}

fn map_trace_summary(row: &rusqlite::Row) -> rusqlite::Result<TraceSummary> {
    let tags_str: Option<String> = row.get("tags")?;
    let tags: Vec<String> = tags_str
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default();

    let root_input: Option<String> = row.get("root_input")?;
    let root_output: Option<String> = row.get("root_output")?;

    Ok(TraceSummary {
        request_id: row.get("request_id")?,
        workflow_name: row.get("workflow_name")?,
        start_time: row.get("start_time")?,
        total_duration_ms: row.get("total_duration_ms")?,
        prompt_tokens: row.get::<_, Option<i64>>("prompt_tokens")?.unwrap_or(0),
        completion_tokens: row.get::<_, Option<i64>>("completion_tokens")?.unwrap_or(0),
        total_tokens: row.get::<_, Option<i64>>("total_tokens")?.unwrap_or(0),
        total_cost: row.get::<_, Option<f64>>("total_cost")?.unwrap_or(0.0),
        node_count: row.get("node_count")?,
        tags,
        input_preview: extract_preview(&root_input),
        output_preview: extract_preview(&root_output),
    })
}

/// Get trace detail with tree structure for a specific request_id.
pub fn get_trace_detail(conn: &Connection, request_id: &str) -> rusqlite::Result<Vec<TraceNode>> {
    let mut stmt = conn.prepare(
        "SELECT * FROM traces WHERE request_id = ?1 ORDER BY execution_order",
    )?;

    let flat_nodes: Vec<TraceNode> = stmt
        .query_map([request_id], |row| map_trace_node(row))?
        .collect::<rusqlite::Result<Vec<_>>>()?;

    Ok(build_tree(flat_nodes))
}

fn map_trace_node(row: &rusqlite::Row) -> rusqlite::Result<TraceNode> {
    let input_str: Option<String> = row.get("input")?;
    let output_str: Option<String> = row.get("output")?;
    let metadata_str: Option<String> = row.get("metadata")?;
    let tags_str: Option<String> = row.get("tags")?;
    let contain_gen: i32 = row.get("contain_generation")?;

    Ok(TraceNode {
        id: row.get("id")?,
        request_id: row.get("request_id")?,
        session_id: row.get("session_id")?,
        op_name: row.get::<_, Option<String>>("op_name")?.unwrap_or_default(),
        op_type: row.get("op_type")?,
        parent_name: row.get("parent_name")?,
        context_id: row.get("context_id")?,
        execution_order: row.get::<_, Option<i64>>("execution_order")?.unwrap_or(0),
        start_time: row.get("start_time")?,
        end_time: row.get("end_time")?,
        duration_ms: row.get("duration_ms")?,
        model: row.get("model")?,
        prompt_tokens: row.get("prompt_tokens")?,
        completion_tokens: row.get("completion_tokens")?,
        total_tokens: row.get("total_tokens")?,
        cost_usd: row.get("cost_usd")?,
        input: input_str.and_then(|s| serde_json::from_str(&s).ok()),
        output: output_str.and_then(|s| serde_json::from_str(&s).ok()),
        contain_generation: contain_gen != 0,
        metadata: metadata_str.and_then(|s| serde_json::from_str(&s).ok()),
        tags: tags_str.and_then(|s| serde_json::from_str(&s).ok()),
        children: vec![],
    })
}

/// Build a tree from flat nodes using parent_name + context_id matching.
/// Ported from ui-hush-eyes-extension/src/database.ts:309-448.
fn build_tree(flat_nodes: Vec<TraceNode>) -> Vec<TraceNode> {
    if flat_nodes.is_empty() {
        return vec![];
    }

    // Build lookup maps
    // Key: "op_name:context_id" or "op_name" (if no context_id)
    let mut node_by_key: HashMap<String, usize> = HashMap::new();
    // Key: "op_name" -> list of indices (for multi-candidate lookup)
    let mut nodes_by_name: HashMap<String, Vec<usize>> = HashMap::new();

    let mut nodes = flat_nodes;

    for (i, node) in nodes.iter().enumerate() {
        let key = if let Some(ref ctx) = node.context_id {
            format!("{}:{}", node.op_name, ctx)
        } else {
            node.op_name.clone()
        };
        node_by_key.insert(key, i);
        nodes_by_name
            .entry(node.op_name.clone())
            .or_default()
            .push(i);
    }

    // Resolve parent-child relationships
    let mut children_map: HashMap<usize, Vec<usize>> = HashMap::new();
    let mut has_parent = vec![false; nodes.len()];

    for i in 0..nodes.len() {
        let parent_name = match &nodes[i].parent_name {
            Some(p) => p.clone(),
            None => continue, // root node
        };

        let context_id = nodes[i].context_id.clone();
        let mut parent_idx: Option<usize> = None;

        if let Some(ref ctx) = context_id {
            // Strategy 1: parent with parent context (drop last .[N] segment)
            let parts: Vec<&str> = ctx.split('.').collect();
            if parts.len() > 1 {
                let parent_ctx = parts[..parts.len() - 1].join(".");
                let key = format!("{}:{}", parent_name, parent_ctx);
                parent_idx = node_by_key.get(&key).copied();
            }

            // Strategy 2: parent with same context_id
            if parent_idx.is_none() {
                let key = format!("{}:{}", parent_name, ctx);
                parent_idx = node_by_key.get(&key).copied();
            }
        }

        // Strategy 3: parent without context (unique name)
        if parent_idx.is_none() {
            parent_idx = node_by_key.get(&parent_name).copied();
        }

        // Strategy 4: multi-candidate fallback with context prefix matching
        if parent_idx.is_none() {
            if let Some(candidates) = nodes_by_name.get(&parent_name) {
                if candidates.len() == 1 {
                    parent_idx = Some(candidates[0]);
                } else if let Some(ref ctx) = context_id {
                    let child_parts: Vec<&str> = ctx.split('.').collect();
                    for &candidate_idx in candidates {
                        if let Some(ref cand_ctx) = nodes[candidate_idx].context_id {
                            let cand_parts: Vec<&str> = cand_ctx.split('.').collect();
                            if child_parts.len() > cand_parts.len() {
                                let prefix = child_parts[..cand_parts.len()].join(".");
                                if prefix == *cand_ctx {
                                    parent_idx = Some(candidate_idx);
                                    break;
                                }
                            }
                        }
                    }
                }
            }
        }

        if let Some(pidx) = parent_idx {
            children_map.entry(pidx).or_default().push(i);
            has_parent[i] = true;
        }
    }

    // Sort children by execution_order
    for children in children_map.values_mut() {
        children.sort_by_key(|&idx| nodes[idx].execution_order);
    }

    // Build tree recursively (bottom-up to avoid borrow issues)
    fn collect_tree(
        idx: usize,
        nodes: &mut Vec<TraceNode>,
        children_map: &HashMap<usize, Vec<usize>>,
        visited: &mut Vec<bool>,
    ) -> TraceNode {
        visited[idx] = true;
        let mut node = nodes[idx].clone();

        if let Some(child_indices) = children_map.get(&idx) {
            for &cidx in child_indices {
                if !visited[cidx] {
                    node.children.push(collect_tree(cidx, nodes, children_map, visited));
                }
            }
        }

        node
    }

    let mut visited = vec![false; nodes.len()];
    let mut roots = Vec::new();

    for i in 0..nodes.len() {
        if !has_parent[i] && !visited[i] {
            roots.push(collect_tree(i, &mut nodes, &children_map, &mut visited));
        }
    }

    roots
}

pub fn clear_all(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute("DELETE FROM traces", [])?;
    Ok(())
}

pub fn delete_by_request_id(conn: &Connection, request_id: &str) -> rusqlite::Result<()> {
    conn.execute("DELETE FROM traces WHERE request_id = ?1", [request_id])?;
    Ok(())
}

/// Extract a meaningful preview string from a JSON value.
/// Ported from ui-hush-eyes-extension/src/database.ts:272-293.
fn extract_preview(json_str: &Option<String>) -> String {
    let s = match json_str {
        Some(s) if !s.is_empty() => s,
        _ => return String::new(),
    };

    let val: serde_json::Value = match serde_json::from_str(s) {
        Ok(v) => v,
        Err(_) => return truncate(s, 200),
    };

    if let Some(s) = val.as_str() {
        return truncate(s, 200);
    }

    if let Some(obj) = val.as_object() {
        // Try common meaningful keys
        for key in &[
            "text", "message", "content", "query", "prompt", "input", "result", "output",
            "response", "answer",
        ] {
            if let Some(v) = obj.get(*key) {
                if let Some(s) = v.as_str() {
                    return truncate(s, 200);
                }
            }
        }
    }

    // Fallback: JSON string
    truncate(s, 200)
}

fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max {
        s.to_string()
    } else {
        format!("{}...", &s[..max])
    }
}
