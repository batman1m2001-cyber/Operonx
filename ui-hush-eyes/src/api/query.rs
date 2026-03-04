use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::Json;

use super::models::*;
use super::AppState;
use crate::db::read;

pub async fn list_traces(
    State(state): State<AppState>,
    Query(params): Query<TraceListParams>,
) -> Result<Json<TraceListResponse>, (StatusCode, String)> {
    let conn = state
        .pool
        .lock()
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    let limit = params.limit.unwrap_or(50);
    let offset = params.offset.unwrap_or(0);
    let page = (offset / limit) + 1;

    let (traces, total) = read::list_traces(&conn, limit, offset, params.time_filter)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    Ok(Json(TraceListResponse {
        traces,
        total,
        page,
    }))
}

pub async fn get_trace_detail(
    State(state): State<AppState>,
    Path(request_id): Path<String>,
) -> Result<Json<TraceDetailResponse>, (StatusCode, String)> {
    let conn = state
        .pool
        .lock()
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    let nodes = read::get_trace_detail(&conn, &request_id)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    Ok(Json(TraceDetailResponse { request_id, nodes }))
}

pub async fn clear_traces(
    State(state): State<AppState>,
) -> Result<Json<StatusResponse>, (StatusCode, String)> {
    let conn = state
        .pool
        .lock()
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    read::clear_all(&conn).map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    Ok(Json(StatusResponse {
        status: "ok".to_string(),
    }))
}

pub async fn delete_trace(
    State(state): State<AppState>,
    Path(request_id): Path<String>,
) -> Result<Json<StatusResponse>, (StatusCode, String)> {
    let conn = state
        .pool
        .lock()
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    read::delete_by_request_id(&conn, &request_id)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    Ok(Json(StatusResponse {
        status: "ok".to_string(),
    }))
}

pub async fn db_info(
    State(state): State<AppState>,
) -> Result<Json<DbInfoResponse>, (StatusCode, String)> {
    let path = &state.db_path;
    let exists = path.exists();
    let size = if exists {
        std::fs::metadata(path).map(|m| m.len()).unwrap_or(0)
    } else {
        0
    };

    Ok(Json(DbInfoResponse {
        path: path.display().to_string(),
        exists,
        size,
    }))
}
