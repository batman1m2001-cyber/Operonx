//! Workflow execution — bridge between Axum async handlers and rush-core's sync engine.

use serde_json::Value;

use rush_core::engine::Rush;

use crate::error::ServeError;

/// Run a workflow on a blocking thread (rush-core is sync/rayon-based).
///
/// Creates a Rush engine from JSON config, runs the graph, and returns the result.
/// Each invocation creates a fresh engine to avoid mutable state leaking between requests.
pub async fn run_workflow(
    graph_json: &str,
    inputs: Value,
    request_id: Option<String>,
) -> Result<Value, ServeError> {
    let gj = graph_json.to_string();

    // Rush::run_json is CPU-bound (rayon parallel ops), so run on blocking thread.
    let result = tokio::task::spawn_blocking(move || {
        let engine = Rush::new(&gj).map_err(|e| ServeError::Execution(e.to_string()))?;
        engine
            .run_json(inputs, request_id, None, None)
            .map_err(|e| ServeError::Execution(e.to_string()))
    })
    .await
    .map_err(|e| ServeError::Internal(format!("Task join error: {}", e)))??;

    Ok(result)
}
