//! [`LangfuseTracer`] — flush traces to Langfuse cloud (or self-hosted).
//!
//! Mirrors Python [`operonx/telemetry/tracers/langfuse.py`](../../../../../operonx/telemetry/tracers/langfuse.py).
//! Accepts either a direct [`LangfuseConfig`] or a `langfuse:<name>`
//! resource key per §6b.3. The client is built lazily on first flush.

use std::sync::{Arc, OnceLock};

use crate::core::exceptions::OperonError;
use crate::core::registry::ResourceHub;
use crate::core::tracing::base::Tracer;
use crate::core::tracing::models::TraceData;
use crate::core::tracing::trace_filter::TraceFilter;
use crate::telemetry::backends::langfuse::{LangfuseClient, LangfuseConfig};

pub struct LangfuseTracer {
    config: Option<LangfuseConfig>,
    resource: Option<String>,
    tags: Vec<String>,
    trace_filter: Option<TraceFilter>,
    client: OnceLock<Arc<LangfuseClient>>,
}

impl std::fmt::Debug for LangfuseTracer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LangfuseTracer")
            .field("resource", &self.resource)
            .field("has_direct_config", &self.config.is_some())
            .finish()
    }
}

impl LangfuseTracer {
    /// Direct-config constructor — produces `to_config_dict() = Some(...)`
    /// per §6b.1.
    pub fn from_config(config: LangfuseConfig, tags: Vec<String>) -> Self {
        let auto_filter = config
            .trace_filter
            .as_ref()
            .map(TraceFilter::from_value);
        Self {
            config: Some(config),
            resource: None,
            tags,
            trace_filter: auto_filter,
            client: OnceLock::new(),
        }
    }

    /// Resource-based constructor — `to_config_dict()` returns `None`
    /// because we can't rehydrate from the Python-side hub (§6b.1).
    pub fn from_resource(resource: impl Into<String>, tags: Vec<String>) -> Self {
        Self {
            config: None,
            resource: Some(resource.into()),
            tags,
            trace_filter: None,
            client: OnceLock::new(),
        }
    }

    pub fn with_trace_filter(mut self, filter: TraceFilter) -> Self {
        self.trace_filter = Some(filter);
        self
    }

    /// Lazy client resolution — fail-late per §6b.9.
    fn get_client(&self) -> Result<Arc<LangfuseClient>, OperonError> {
        if let Some(c) = self.client.get() {
            return Ok(c.clone());
        }
        let client = if let Some(cfg) = &self.config {
            LangfuseClient::new(cfg.clone())
        } else {
            // Resource path: look up `<category>:<name>` in the hub.
            let key = self
                .resource
                .as_deref()
                .expect("config|resource XOR validated at ctor");
            let full_key = if key.starts_with("langfuse:") {
                key.to_string()
            } else {
                format!("langfuse:{}", key)
            };
            let hub = ResourceHub::instance()?;
            let instance = hub.get(&full_key)?;
            let wrapper: Arc<crate::telemetry::plugin::LangfuseResource> = instance
                .downcast::<crate::telemetry::plugin::LangfuseResource>()
                .map_err(|_| {
                    OperonError::ResourceHub(format!(
                        "resource '{}' is not a LangfuseClient (downcast failed)",
                        full_key
                    ))
                })?;
            return Ok(self
                .client
                .get_or_init(|| wrapper.0.clone())
                .clone());
        };
        Ok(self.client.get_or_init(|| Arc::new(client)).clone())
    }
}

impl Tracer for LangfuseTracer {
    fn name(&self) -> &str {
        "langfuse"
    }

    fn tags(&self) -> &[String] {
        &self.tags
    }

    fn trace_filter(&self) -> Option<&TraceFilter> {
        self.trace_filter.as_ref()
    }

    fn to_config_dict(&self) -> Option<serde_json::Value> {
        match &self.config {
            Some(cfg) => serde_json::to_value(cfg).ok(),
            None => None,
        }
    }

    fn flush(&self, trace: &TraceData) -> Result<(), OperonError> {
        let client = self.get_client()?;
        client.ingest(trace)
    }
}
