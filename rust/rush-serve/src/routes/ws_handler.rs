//! WS /path/ws — bidirectional WebSocket handler.
//!
//! Protocol:
//!   Client → {"inputs": {...}}           — run workflow
//!   Server → {"type": "result", "data": {...}}  — workflow result
//!   Client → {"type": "close"}           — disconnect

use axum::extract::ws::{Message, WebSocket};
use serde_json::{json, Value};

use crate::execute::run_workflow;
use crate::routes::sync_handler::filter_internal_keys;
use crate::state::AppState;

pub async fn handle_ws(app: AppState, path: String, mut socket: WebSocket) {
    while let Some(Ok(msg)) = socket.recv().await {
        match msg {
            Message::Text(text) => {
                let parsed: Value = match serde_json::from_str(&text) {
                    Ok(v) => v,
                    Err(e) => {
                        let err = json!({"type": "error", "data": {"error": e.to_string()}});
                        let _ = socket.send(Message::Text(err.to_string().into())).await;
                        continue;
                    }
                };

                // Check for close message
                if parsed.get("type").and_then(|v| v.as_str()) == Some("close") {
                    break;
                }

                // Extract inputs
                let inputs = parsed
                    .get("inputs")
                    .cloned()
                    .unwrap_or(Value::Object(serde_json::Map::new()));

                let ep = match app.get_endpoint(&path) {
                    Some(ep) => ep,
                    None => {
                        let err = json!({"type": "error", "data": {"error": "endpoint not found"}});
                        let _ = socket.send(Message::Text(err.to_string().into())).await;
                        continue;
                    }
                };

                let request_id = uuid::Uuid::new_v4().to_string();
                match run_workflow(ep.config.clone(), inputs, Some(request_id), app.tracers.clone()).await {
                    Ok(result) => {
                        let filtered = filter_internal_keys(result);
                        let msg = json!({"type": "result", "data": filtered});
                        let _ = socket.send(Message::Text(msg.to_string().into())).await;
                    }
                    Err(e) => {
                        let msg = json!({"type": "error", "data": {"error": e.to_string()}});
                        let _ = socket.send(Message::Text(msg.to_string().into())).await;
                    }
                }
            }
            Message::Close(_) => break,
            _ => {}
        }
    }
}
