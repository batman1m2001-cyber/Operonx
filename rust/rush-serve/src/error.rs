//! Error types for rush-serve.

use axum::http::StatusCode;
use axum::response::{IntoResponse, Json, Response};
use serde_json::json;

/// Server error type.
#[derive(Debug, thiserror::Error)]
pub enum ServeError {
    #[error("Workflow execution failed: {0}")]
    Execution(String),

    #[error("Invalid input: {0}")]
    Validation(String),

    #[error("Job not found: {0}")]
    JobNotFound(String),

    #[error("Internal error: {0}")]
    Internal(String),
}

impl IntoResponse for ServeError {
    fn into_response(self) -> Response {
        let (status, message) = match &self {
            ServeError::Execution(msg) => (StatusCode::INTERNAL_SERVER_ERROR, msg.clone()),
            ServeError::Validation(msg) => (StatusCode::BAD_REQUEST, msg.clone()),
            ServeError::JobNotFound(msg) => (StatusCode::NOT_FOUND, msg.clone()),
            ServeError::Internal(msg) => (StatusCode::INTERNAL_SERVER_ERROR, msg.clone()),
        };

        let body = json!({
            "error": message,
            "status": status.as_u16(),
        });

        (status, Json(body)).into_response()
    }
}

impl From<rush_core::error::RushError> for ServeError {
    fn from(e: rush_core::error::RushError) -> Self {
        ServeError::Execution(e.to_string())
    }
}
