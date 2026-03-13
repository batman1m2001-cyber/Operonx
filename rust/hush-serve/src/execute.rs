//! Workflow execution — bridge between Axum async handlers and hush-icore's sync engine.

use std::sync::Arc;

use serde_json::Value;

use hush_icore::config::GraphConfig;
use hush_icore::engine::Hush;
use hush_icore::registry::OpRegistry;
use hush_icore::tracing::Tracer;

use crate::config::TracerConfig;
use crate::error::ServeError;

/// Build tracers from server config.
pub fn build_tracers(config: &Option<TracerConfig>) -> Vec<Arc<dyn Tracer>> {
    let config = match config {
        Some(c) => c,
        None => return vec![],
    };

    let mut tracers: Vec<Arc<dyn Tracer>> = Vec::new();

    // HushEyes tracer
    if let Some(ref he) = config.hush_eyes {
        let tracer = hush_telemetry::HushEyesTracer::new(
            Some(&he.host),
            Some(he.port),
            vec![],
        );
        tracers.push(Arc::new(tracer));
    }

    // Langfuse tracer
    if let Some(ref lf) = config.langfuse {
        // Try config values first, fall back to env vars
        let public_key = lf
            .public_key
            .clone()
            .or_else(|| std::env::var("LANGFUSE_PUBLIC_KEY").ok());
        let secret_key = lf
            .secret_key
            .clone()
            .or_else(|| std::env::var("LANGFUSE_SECRET_KEY").ok());
        let host = lf
            .host
            .clone()
            .or_else(|| std::env::var("LANGFUSE_HOST").ok())
            .unwrap_or_else(|| "https://cloud.langfuse.com".to_string());

        if let (Some(pk), Some(sk)) = (public_key, secret_key) {
            let lf_config = hush_telemetry::langfuse::config::LangfuseConfig {
                public_key: pk,
                secret_key: sk,
                host,
            };
            let tracer = hush_telemetry::langfuse::LangfuseTracer::new(
                lf_config,
                lf.stream_trace_limit,
            );
            tracers.push(Arc::new(tracer));
        } else {
            log::warn!("Langfuse tracer configured but missing public_key/secret_key");
        }
    }

    tracers
}

/// Run a workflow on a blocking thread using a pre-parsed config.
///
/// Uses Hush::from_config() to skip JSON parsing — config was parsed once at startup.
pub async fn run_workflow(
    config: Arc<GraphConfig>,
    inputs: Value,
    request_id: Option<String>,
    tracers: Vec<Arc<dyn Tracer>>,
    registry: Option<Arc<dyn OpRegistry>>,
) -> Result<Value, ServeError> {
    let result = tokio::task::spawn_blocking(move || {
        let mut engine = Hush::from_config(config);

        for tracer in tracers {
            engine.add_tracer_arc(tracer);
        }

        if let Some(reg) = registry {
            engine.set_registry(reg);
        }

        engine
            .run_json(inputs, request_id, None, None)
            .map_err(|e| ServeError::Execution(e.to_string()))
    })
    .await
    .map_err(|e| ServeError::Internal(format!("Task join error: {}", e)))??;

    Ok(result)
}
