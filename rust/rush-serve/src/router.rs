//! Router builder — creates Axum routes from endpoint definitions.

use std::sync::Arc;

use axum::extract::ws::WebSocketUpgrade;
use axum::response::Json;
use axum::routing::{get, post};
use axum::Router;
use serde_json::{json, Value};
use tower_http::cors::{Any, CorsLayer};

use crate::config::ServerConfig;
use crate::error::ServeError;
use crate::routes::{stream_handler, sync_handler, ws_handler};
use crate::state::{AppState, EndpointState};

/// Build the Axum router from a server config.
pub fn build_router(config: ServerConfig) -> Router {
    let tracers = crate::execute::build_tracers(&config.tracers);
    let app_state = AppState::new(tracers);
    let mut endpoint_paths: Vec<String> = Vec::new();

    for ep_def in config.endpoints {
        let path = ep_def.path.clone();
        endpoint_paths.push(path.clone());

        let graph_json = serde_json::to_string(&ep_def.graph).unwrap_or_default();

        let ep_state = Arc::new(EndpointState {
            graph_json,
            def: ep_def,
        });
        app_state.endpoints.insert(path.clone(), ep_state);
    }

    let mut router = Router::new();

    // Generate routes for each endpoint
    for path in &endpoint_paths {
        let p = path.clone();
        let state = app_state.clone();

        // POST /path — sync
        let sync_path = p.clone();
        let sync_state = state.clone();
        router = router.route(
            &p,
            post(move |Json(inputs): Json<Value>| async move {
                let ep = sync_state
                    .get_endpoint(&sync_path)
                    .ok_or_else(|| ServeError::Internal("endpoint not found".into()))?;
                let request_id = uuid::Uuid::new_v4().to_string();
                let result =
                    crate::execute::run_workflow(&ep.graph_json, inputs, Some(request_id), sync_state.tracers.clone()).await?;
                Ok::<Json<Value>, ServeError>(Json(sync_handler::filter_internal_keys(result)))
            }),
        );

        // POST /path/stream — SSE
        let ep_state_for_check = state.get_endpoint(&p);
        let has_stream = ep_state_for_check
            .as_ref()
            .map(|e| e.def.stream.unwrap_or(false))
            .unwrap_or(false);

        if has_stream {
            let stream_path = format!("{}/stream", p);
            let stream_state = state.clone();
            let sp = p.clone();
            router = router.route(
                &stream_path,
                post(move |Json(inputs): Json<Value>| async move {
                    stream_handler::handle_stream(stream_state, sp, inputs).await
                }),
            );
        }

        // WS /path/ws — WebSocket
        let has_ws = ep_state_for_check
            .as_ref()
            .map(|e| e.def.websocket)
            .unwrap_or(false);

        if has_ws {
            let ws_path = format!("{}/ws", p);
            let ws_state = state.clone();
            let wp = p.clone();
            router = router.route(
                &ws_path,
                get(move |ws: WebSocketUpgrade| async move {
                    let s = ws_state.clone();
                    let pp = wp.clone();
                    ws.on_upgrade(move |socket| ws_handler::handle_ws(s, pp, socket))
                }),
            );
        }
    }

    // GET /health
    let paths = endpoint_paths.clone();
    router = router.route(
        "/health",
        get(move || async move {
            Json(json!({
                "status": "ok",
                "backend": "rust",
                "endpoints": paths,
            }))
        }),
    );

    // GET /endpoints
    let ep_state_ref = app_state.clone();
    let all_paths = endpoint_paths.clone();
    router = router.route(
        "/endpoints",
        get(move || async move {
            let mut infos = Vec::new();
            for p in &all_paths {
                if let Some(ep) = ep_state_ref.get_endpoint(p) {
                    infos.push(json!({
                        "path": p,
                        "stream": ep.def.stream,
                        "websocket": ep.def.websocket,
                    }));
                }
            }
            Json(json!({ "endpoints": infos }))
        }),
    );

    // CORS
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    router.layer(cors)
}
