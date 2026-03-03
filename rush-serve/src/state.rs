//! Shared application state.

use std::sync::Arc;

use dashmap::DashMap;

use crate::config::EndpointDef;
use crate::jobs::JobStore;

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
    pub job_store: Arc<JobStore>,
}

impl AppState {
    pub fn new() -> Self {
        AppState {
            endpoints: Arc::new(DashMap::new()),
            job_store: Arc::new(JobStore::new()),
        }
    }

    pub fn get_endpoint(&self, path: &str) -> Option<Arc<EndpointState>> {
        self.endpoints.get(path).map(|e| Arc::clone(e.value()))
    }
}
