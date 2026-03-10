//! LangfuseClient — direct HTTP API, no SDK dependency.
//!
//! Uses POST /api/public/ingestion with Basic auth for trace ingestion.

use serde_json::Value;

use super::config::LangfuseConfig;

/// Low-level Langfuse HTTP client for batch ingestion.
#[derive(Debug)]
pub struct LangfuseClient {
    config: LangfuseConfig,
    auth: String,
    ingest_url: String,
    http: reqwest::blocking::Client,
}

impl LangfuseClient {
    pub fn new(config: LangfuseConfig) -> Self {
        let auth = config.basic_auth();
        let ingest_url = config.ingest_url();
        Self {
            config,
            auth,
            ingest_url,
            http: reqwest::blocking::Client::builder()
                .timeout(std::time::Duration::from_secs(30))
                .build()
                .expect("Failed to build HTTP client"),
        }
    }

    /// Send a batch of ingestion events to Langfuse.
    ///
    /// Returns the response JSON with `successes` and `errors` fields.
    pub fn ingest(&self, batch: Vec<Value>) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
        let body = serde_json::json!({
            "batch": batch,
            "metadata": {
                "batch_size": batch.len(),
                "sdk_integration": "default",
                "sdk_name": "rust",
                "sdk_version": "rush",
                "public_key": self.config.public_key,
            }
        });

        let resp = self
            .http
            .post(&self.ingest_url)
            .header("Content-Type", "application/json")
            .header("Authorization", format!("Basic {}", self.auth))
            .header("x_langfuse_sdk_name", "rust")
            .header("x_langfuse_sdk_version", "rush")
            .header("x_langfuse_public_key", &self.config.public_key)
            .json(&body)
            .send()?;

        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().unwrap_or_default();
            return Err(format!("Langfuse API returned {}: {}", status, text).into());
        }

        let result: Value = resp.json()?;
        Ok(result)
    }

    /// Build the Langfuse UI URL for a trace.
    pub fn trace_url(&self, trace_id: &str) -> String {
        self.config.trace_url(trace_id)
    }
}
