use axum::extract::State;
use axum::http::StatusCode;
use axum::Json;

use super::models::{IngestRequest, IngestResponse};
use super::AppState;
use crate::db::write;

pub async fn handle_ingest(
    State(state): State<AppState>,
    Json(req): Json<IngestRequest>,
) -> Result<Json<IngestResponse>, (StatusCode, String)> {
    let conn = state
        .pool
        .lock()
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    let count = write::ingest_traces(&conn, &req)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    Ok(Json(IngestResponse {
        status: "ok".to_string(),
        rows_inserted: count,
    }))
}
