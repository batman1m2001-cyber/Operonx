use axum::Router;
use std::path::Path;
use tower_http::cors::{Any, CorsLayer};
use tower_http::services::ServeDir;

use crate::db::DbPool;

pub mod ingest;
pub mod models;
pub mod query;

pub fn router(pool: DbPool, db_path: &Path) -> Router {
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let api = Router::new()
        .route("/api/ingest", axum::routing::post(ingest::handle_ingest))
        .route("/api/traces", axum::routing::get(query::list_traces))
        .route(
            "/api/traces",
            axum::routing::delete(query::clear_traces),
        )
        .route(
            "/api/traces/{request_id}",
            axum::routing::get(query::get_trace_detail),
        )
        .route(
            "/api/traces/{request_id}",
            axum::routing::delete(query::delete_trace),
        )
        .route("/api/db-info", axum::routing::get(query::db_info))
        .with_state(AppState {
            pool,
            db_path: db_path.to_path_buf(),
        });

    // Serve static files at root, with API routes taking priority
    let static_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("static");
    let serve_dir = ServeDir::new(static_dir).append_index_html_on_directories(true);

    api.fallback_service(serve_dir).layer(cors)
}

#[derive(Clone)]
pub struct AppState {
    pub pool: DbPool,
    pub db_path: std::path::PathBuf,
}
