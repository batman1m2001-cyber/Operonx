//! POST /path/batch — concurrent batch execution.

use axum::response::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::ServeError;
use crate::execute::run_workflow;
use crate::routes::sync_handler::filter_internal_keys;
use crate::state::AppState;

#[derive(Deserialize)]
pub struct BatchRequest {
    pub items: Vec<Value>,
}

pub async fn handle_batch(
    app: AppState,
    path: String,
    batch: BatchRequest,
) -> Result<Json<Value>, ServeError> {
    let ep = app
        .get_endpoint(&path)
        .ok_or_else(|| ServeError::Internal(format!("endpoint '{}' not found", path)))?;

    let max_batch = ep.def.max_batch_size;
    if batch.items.len() > max_batch {
        return Err(ServeError::Validation(format!(
            "Batch size {} exceeds max {}",
            batch.items.len(),
            max_batch
        )));
    }

    let concurrency = ep.def.batch_concurrency;
    let graph_json = ep.graph_json.clone();
    let semaphore = std::sync::Arc::new(tokio::sync::Semaphore::new(concurrency));

    let mut handles = Vec::with_capacity(batch.items.len());
    for item in batch.items {
        let sem = semaphore.clone();
        let gj = graph_json.clone();
        handles.push(tokio::spawn(async move {
            let _permit = sem.acquire().await.unwrap();
            match run_workflow(&gj, item, None).await {
                Ok(result) => filter_internal_keys(result),
                Err(e) => json!({ "error": e.to_string() }),
            }
        }));
    }

    let mut results = Vec::with_capacity(handles.len());
    for handle in handles {
        results.push(handle.await.unwrap_or_else(|e| json!({ "error": e.to_string() })));
    }

    Ok(Json(json!({
        "results": results,
        "total": results.len(),
    })))
}
