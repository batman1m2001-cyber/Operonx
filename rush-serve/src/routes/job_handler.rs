//! POST /path/submit — submit background job.
//! GET /jobs/{job_id} — check job status.

use axum::response::Json;
use serde_json::{json, Value};

use crate::error::ServeError;
use crate::execute::run_workflow;
use crate::jobs::JobStore;
use crate::routes::sync_handler::filter_internal_keys;
use crate::state::AppState;

pub async fn handle_submit(
    app: AppState,
    path: String,
    inputs: Value,
) -> Result<Json<Value>, ServeError> {
    let ep = app
        .get_endpoint(&path)
        .ok_or_else(|| ServeError::Internal(format!("endpoint '{}' not found", path)))?;

    let job_id = uuid::Uuid::new_v4().to_string();
    app.job_store.create(job_id.clone());

    let graph_json = ep.graph_json.clone();
    let store = app.job_store.clone();
    let jid = job_id.clone();

    let tracers = app.tracers.clone();
    tokio::spawn(async move {
        store.set_running(&jid);
        match run_workflow(&graph_json, inputs, Some(jid.clone()), tracers).await {
            Ok(result) => store.set_completed(&jid, filter_internal_keys(result)),
            Err(e) => store.set_failed(&jid, e.to_string()),
        }
    });

    Ok(Json(json!({ "job_id": job_id })))
}

pub async fn handle_job_status(
    store: std::sync::Arc<JobStore>,
    job_id: String,
) -> Result<Json<Value>, ServeError> {
    let job = store
        .get(&job_id)
        .ok_or_else(|| ServeError::JobNotFound(job_id))?;

    Ok(Json(json!({
        "job_id": job.id,
        "status": job.status,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at.to_rfc3339(),
        "completed_at": job.completed_at.map(|t| t.to_rfc3339()),
    })))
}
