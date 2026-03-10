//! POST /path/stream — Server-Sent Events streaming.
//!
//! Since rush-core v1 doesn't support streaming internally (LLM ops return
//! full results), we simulate SSE by running the workflow and sending the
//! final result as a single SSE event. When rush-core adds native streaming,
//! this handler will emit incremental chunks.

use axum::response::sse::{Event, Sse};
use futures::stream;
use serde_json::Value;

use crate::error::ServeError;
use crate::execute::run_workflow;
use crate::routes::sync_handler::filter_internal_keys;
use crate::state::AppState;

pub async fn handle_stream(
    app: AppState,
    path: String,
    inputs: Value,
) -> Result<Sse<impl tokio_stream::Stream<Item = Result<Event, ServeError>>>, ServeError> {
    let ep = app
        .get_endpoint(&path)
        .ok_or_else(|| ServeError::Internal(format!("endpoint '{}' not found", path)))?;

    let graph_json = ep.graph_json.clone();
    let request_id = uuid::Uuid::new_v4().to_string();

    // Run workflow (blocking in Rust mode — full result at once).
    // When rush-core supports incremental output, we'll stream chunks here.
    let result = run_workflow(&graph_json, inputs, Some(request_id), app.tracers.clone()).await?;
    let filtered = filter_internal_keys(result);

    // Emit as SSE: one "result" event with the full output.
    let events = vec![
        Ok(Event::default()
            .event("result")
            .json_data(&filtered)
            .unwrap()),
        Ok(Event::default().event("done").data("[DONE]")),
    ];

    let stream = stream::iter(events);
    Ok(Sse::new(stream))
}
