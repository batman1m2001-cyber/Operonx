//! [`ConfigurableTracer`] helper — tracers backed by either a direct config
//! or a [`ResourceHub`] resource key.
//!
//! Mirrors Python [`operon/telemetry/tracers/_base.py`](../../../../../operon/telemetry/tracers/_base.py).
//! Per §6b.3 resource lookup is **lazy** — construction just stores the
//! string; `get_client()` fires on first `flush()`.

use std::marker::PhantomData;
use std::sync::{Arc, OnceLock};

use crate::core::exceptions::OperonError;
use crate::core::registry::ResourceHub;
use crate::core::tracing::trace_filter::TraceFilter;

/// Holder shared by every `ConfigurableTracer`-shaped Rust tracer — the two
/// construction paths (direct config vs. resource key) plus a once-only
/// client cache.
pub struct ConfigurableInner<Cfg, Client> {
    pub config: Option<Cfg>,
    pub resource: Option<String>,
    pub tags: Vec<String>,
    pub trace_filter: Option<TraceFilter>,
    pub client: OnceLock<Arc<Client>>,
    _phantom: PhantomData<Client>,
}

impl<Cfg, Client> std::fmt::Debug for ConfigurableInner<Cfg, Client>
where
    Cfg: std::fmt::Debug,
{
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ConfigurableInner")
            .field("config", &self.config.as_ref().map(|_| "<set>"))
            .field("resource", &self.resource)
            .field("tags", &self.tags)
            .field("has_client", &self.client.get().is_some())
            .finish()
    }
}

impl<Cfg, Client> ConfigurableInner<Cfg, Client> {
    /// Validate the XOR constraint — exactly one of `config` / `resource`
    /// must be set.
    pub fn new(
        config: Option<Cfg>,
        resource: Option<String>,
        tags: Vec<String>,
        trace_filter: Option<TraceFilter>,
    ) -> Result<Self, OperonError> {
        match (&config, &resource) {
            (None, None) => Err(OperonError::Config(
                "tracer must be constructed with either `config` or `resource`".into(),
            )),
            (Some(_), Some(_)) => Err(OperonError::Config(
                "tracer cannot be constructed with both `config` and `resource`".into(),
            )),
            _ => Ok(Self {
                config,
                resource,
                tags,
                trace_filter,
                client: OnceLock::new(),
                _phantom: PhantomData,
            }),
        }
    }

    /// Lazy client accessor. `make_client` runs at most once.
    pub fn get_or_init_client<F>(&self, make_client: F) -> Result<Arc<Client>, OperonError>
    where
        F: FnOnce(&Cfg) -> Result<Client, OperonError>,
        Client: 'static,
    {
        if let Some(c) = self.client.get() {
            return Ok(c.clone());
        }
        let built = if let Some(cfg) = &self.config {
            Arc::new(make_client(cfg)?)
        } else {
            // Resource-based path — look up via the hub. Downcast to
            // `Arc<Client>` at the call site (each concrete tracer handles
            // its own downcast).
            return Err(OperonError::ResourceHub(format!(
                "{}: resource-based tracer construction is supported but the concrete client downcast \
                 is resolved in each tracer's flush path (Phase 7b)",
                std::any::type_name::<Client>()
            )));
        };
        Ok(self.client.get_or_init(|| built).clone())
    }

    /// Resolve a resource-based client by downcasting the `ResourceHub`
    /// instance handle; returns a typed `Arc<Client>`.
    pub fn resolve_resource_client(
        &self,
        category_prefix: &str,
    ) -> Result<Arc<dyn std::any::Any + Send + Sync>, OperonError>
    where
        Client: 'static,
    {
        let resource = self
            .resource
            .as_deref()
            .ok_or_else(|| OperonError::Config("no resource key set".into()))?;
        let full_key = if resource.starts_with(&format!("{}:", category_prefix)) {
            resource.to_string()
        } else {
            format!("{}:{}", category_prefix, resource)
        };
        ResourceHub::instance()?.get(&full_key)
    }
}
