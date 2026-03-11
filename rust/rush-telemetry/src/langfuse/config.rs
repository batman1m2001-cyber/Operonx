//! LangfuseConfig — configuration for Langfuse backend.

use base64::{engine::general_purpose::STANDARD, Engine};

/// Configuration for connecting to the Langfuse API.
#[derive(Debug, Clone)]
pub struct LangfuseConfig {
    pub public_key: String,
    pub secret_key: String,
    pub host: String,
}

impl LangfuseConfig {
    /// Create config from environment variables.
    ///
    /// Reads: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (optional).
    pub fn from_env() -> Result<Self, std::env::VarError> {
        Ok(Self {
            public_key: std::env::var("LANGFUSE_PUBLIC_KEY")?,
            secret_key: std::env::var("LANGFUSE_SECRET_KEY")?,
            host: std::env::var("LANGFUSE_HOST")
                .unwrap_or_else(|_| "https://cloud.langfuse.com".to_string()),
        })
    }

    /// Build the Basic auth header value.
    pub(crate) fn basic_auth(&self) -> String {
        let creds = format!("{}:{}", self.public_key, self.secret_key);
        STANDARD.encode(creds.as_bytes())
    }

    /// Build the ingestion API URL.
    pub(crate) fn ingest_url(&self) -> String {
        format!("{}/api/public/ingestion", self.host.trim_end_matches('/'))
    }

    /// Build the Langfuse UI URL for a trace.
    pub fn trace_url(&self, trace_id: &str) -> String {
        format!("{}/trace/{}", self.host.trim_end_matches('/'), trace_id)
    }
}
