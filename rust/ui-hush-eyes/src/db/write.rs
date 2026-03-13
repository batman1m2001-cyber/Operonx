use rusqlite::Connection;
use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::api::models::IngestRequest;

pub fn ingest_traces(conn: &Connection, req: &IngestRequest) -> rusqlite::Result<usize> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();

    // Build lookup from op_name -> graph node (static metadata)
    let graph_map: HashMap<&str, _> = req
        .graph_structure
        .iter()
        .map(|n| (n.op_name.as_str(), n))
        .collect();

    let tags_json = req
        .tags
        .as_ref()
        .map(|t| serde_json::to_string(t).unwrap_or_default());

    let mut count = 0;

    let tx = conn.unchecked_transaction()?;

    {
        let mut stmt = tx.prepare_cached(
            "INSERT INTO traces (
                request_id, workflow_name, op_name, op_type, parent_name,
                context_id, execution_order, start_time, end_time, duration_ms,
                model, prompt_tokens, completion_tokens, total_tokens, cost_usd,
                input, output, user_id, session_id, contain_generation,
                metadata, tags, status, created_at
            ) VALUES (
                ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10,
                ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20,
                ?21, ?22, 'flushed', ?23
            )",
        )?;

        for (i, record) in req.records.iter().enumerate() {
            // Look up static metadata from graph_structure
            let graph_node = graph_map.get(record.op_name.as_str());

            let op_type = graph_node.and_then(|n| n.op_type.as_deref());
            let parent_name = record
                .metadata
                .as_ref()
                .and_then(|m| m.get("parent_name"))
                .and_then(|v| v.as_str())
                .or_else(|| graph_node.and_then(|n| n.parent_name.as_deref()));
            let contain_generation = graph_node
                .and_then(|n| n.contain_generation)
                .unwrap_or(false);

            let input_json = record
                .inputs
                .as_ref()
                .map(|v| serde_json::to_string(v).unwrap_or_default());
            let output_json = record
                .outputs
                .as_ref()
                .map(|v| serde_json::to_string(v).unwrap_or_default());
            let metadata_json = record
                .metadata
                .as_ref()
                .map(|v| serde_json::to_string(v).unwrap_or_default());

            let prompt_tokens = record.usage.as_ref().and_then(|u| u.prompt_tokens);
            let completion_tokens = record.usage.as_ref().and_then(|u| u.completion_tokens);
            let total_tokens = record.usage.as_ref().and_then(|u| u.total_tokens);

            stmt.execute(rusqlite::params![
                req.request_id,
                req.workflow_name,
                record.op_name,
                op_type,
                parent_name,
                record.context_id,
                i as i64,             // execution_order
                record.start_time,
                record.end_time,
                record.duration_ms,
                record.model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                record.cost,
                input_json,
                output_json,
                req.user_id,
                req.session_id,
                contain_generation as i32,
                metadata_json,
                tags_json,
                now,
            ])?;

            count += 1;
        }
    }

    tx.commit()?;
    Ok(count)
}
