//! Shared application state.

use std::sync::Arc;

use dashmap::DashMap;

use rush_core::tracing::Tracer;

use crate::config::EndpointDef;

/// Per-endpoint runtime state: parsed config + metadata.
pub struct EndpointState {
    /// The raw graph config JSON string (for Rush::new()).
    pub graph_json: String,
    /// Endpoint definition from the config file.
    pub def: EndpointDef,
}

/// Shared application state, accessible from all handlers via Axum State extractor.
#[derive(Clone)]
pub struct AppState {
    pub endpoints: Arc<DashMap<String, Arc<EndpointState>>>,
    pub tracers: Vec<Arc<dyn Tracer>>,
}

impl AppState {
    pub fn new(tracers: Vec<Arc<dyn Tracer>>) -> Self {
        AppState {
            endpoints: Arc::new(DashMap::new()),
            tracers,
        }
    }

    pub fn get_endpoint(&self, path: &str) -> Option<Arc<EndpointState>> {
        self.endpoints.get(path).map(|e| Arc::clone(e.value()))
    }
}
