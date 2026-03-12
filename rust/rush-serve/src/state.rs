//! Shared application state.

use std::sync::Arc;

use dashmap::DashMap;

use rush_core::config::GraphConfig;
use rush_core::registry::OpRegistry;
use rush_core::tracing::Tracer;

use crate::config::EndpointDef;

/// Per-endpoint runtime state: pre-parsed config + metadata.
pub struct EndpointState {
    /// Pre-parsed graph config (parsed once at startup, reused per request).
    pub config: Arc<GraphConfig>,
    /// Endpoint definition from the config file.
    pub def: EndpointDef,
}

/// Shared application state, accessible from all handlers via Axum State extractor.
#[derive(Clone)]
pub struct AppState {
    pub endpoints: Arc<DashMap<String, Arc<EndpointState>>>,
    pub tracers: Vec<Arc<dyn Tracer>>,
    pub registry: Option<Arc<dyn OpRegistry>>,
}

impl AppState {
    pub fn new(tracers: Vec<Arc<dyn Tracer>>, registry: Option<Arc<dyn OpRegistry>>) -> Self {
        AppState {
            endpoints: Arc::new(DashMap::new()),
            tracers,
            registry,
        }
    }

    pub fn get_endpoint(&self, path: &str) -> Option<Arc<EndpointState>> {
        self.endpoints.get(path).map(|e| Arc::clone(e.value()))
    }
}
