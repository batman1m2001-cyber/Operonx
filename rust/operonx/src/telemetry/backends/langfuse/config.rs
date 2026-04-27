//! Langfuse configuration.
//!
//! Mirrors Python [`operonx/telemetry/backends/langfuse/config.py`](../../../../../../operonx/telemetry/backends/langfuse/config.py).

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::exceptions::OperonError;

fn default_host() -> String {
    "https://cloud.langfuse.com".to_string()
}
fn default_enabled() -> bool {
    true
}
fn default_sample_rate() -> f32 {
    1.0
}

/// Configuration for the Langfuse observability backend.
///
/// `ResourceHub` keys are `langfuse:<name>` (e.g. `langfuse:default`) — see
/// §6b.7 for the category-discriminator convention.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LangfuseConfig {
    pub public_key: String,
    pub secret_key: String,

    #[serde(default = "default_host")]
    pub host: String,

    #[serde(default)]
    pub no_proxy: Option<String>,

    #[serde(default = "default_enabled")]
    pub enabled: bool,

    #[serde(default = "default_sample_rate")]
    pub sample_rate: f32,

    /// Optional `trace_filter` block that `ConfigurableTracer` auto-parses
    /// into a [`TraceFilter`](crate::core::tracing::TraceFilter) — see
    /// `_load_trace_filter` in Python's `_base.py`.
    #[serde(default)]
    pub trace_filter: Option<Value>,
}

impl LangfuseConfig {
    /// Category used as the `ResourceHub` key prefix.
    pub const CATEGORY: &'static str = "langfuse";

    /// Read `LANGFUSE_*` env vars. Fails with a typed error if either required
    /// key is missing.
    pub fn from_env() -> Result<Self, OperonError> {
        let public_key = std::env::var("LANGFUSE_PUBLIC_KEY").map_err(|_| {
            OperonError::Config("LangfuseConfig::from_env: LANGFUSE_PUBLIC_KEY not set".into())
        })?;
        let secret_key = std::env::var("LANGFUSE_SECRET_KEY").map_err(|_| {
            OperonError::Config("LangfuseConfig::from_env: LANGFUSE_SECRET_KEY not set".into())
        })?;
        Ok(Self {
            public_key,
            secret_key,
            host: std::env::var("LANGFUSE_HOST").unwrap_or_else(|_| default_host()),
            no_proxy: std::env::var("NO_PROXY").ok(),
            enabled: true,
            sample_rate: 1.0,
            trace_filter: None,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_yaml_shape_with_defaults() {
        let src = r#"{"public_key": "pk", "secret_key": "sk"}"#;
        let cfg: LangfuseConfig = serde_json::from_str(src).unwrap();
        assert_eq!(cfg.host, "https://cloud.langfuse.com");
        assert!(cfg.enabled);
        assert_eq!(cfg.sample_rate, 1.0);
    }
}
