//! OtelConfig — configuration for OpenTelemetry backend.

use std::collections::HashMap;

/// Configuration for OpenTelemetry trace export.
#[derive(Debug, Clone)]
pub struct OtelConfig {
    pub endpoint: String,
    pub protocol: OtelProtocol,
    pub headers: HashMap<String, String>,
    pub service_name: String,
    pub service_version: Option<String>,
    pub insecure: bool,
    pub timeout_secs: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OtelProtocol {
    Grpc,
    Http,
}

impl OtelConfig {
    /// Create config from environment variables.
    ///
    /// Reads: `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL`,
    /// `OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_SERVICE_NAME`, `OTEL_SERVICE_VERSION`.
    pub fn from_env() -> Result<Self, std::env::VarError> {
        let protocol = match std::env::var("OTEL_EXPORTER_OTLP_PROTOCOL")
            .unwrap_or_else(|_| "grpc".to_string())
            .as_str()
        {
            "http" => OtelProtocol::Http,
            _ => OtelProtocol::Grpc,
        };

        let headers = std::env::var("OTEL_EXPORTER_OTLP_HEADERS")
            .ok()
            .map(|s| {
                s.split(',')
                    .filter_map(|pair| {
                        let mut parts = pair.splitn(2, '=');
                        Some((parts.next()?.to_string(), parts.next()?.to_string()))
                    })
                    .collect()
            })
            .unwrap_or_default();

        Ok(Self {
            endpoint: std::env::var("OTEL_EXPORTER_OTLP_ENDPOINT")?,
            protocol,
            headers,
            service_name: std::env::var("OTEL_SERVICE_NAME")
                .unwrap_or_else(|_| "rush-workflow".to_string()),
            service_version: std::env::var("OTEL_SERVICE_VERSION").ok(),
            insecure: false,
            timeout_secs: 30,
        })
    }

    /// Create config for a local Jaeger instance.
    pub fn jaeger(host: &str, port: u16) -> Self {
        Self {
            endpoint: format!("http://{}:{}", host, port),
            protocol: OtelProtocol::Grpc,
            headers: HashMap::new(),
            service_name: "rush-workflow".to_string(),
            service_version: None,
            insecure: true,
            timeout_secs: 30,
        }
    }

    /// Create config for Grafana Tempo.
    pub fn tempo(endpoint: &str, api_key: Option<&str>) -> Self {
        let mut headers = HashMap::new();
        if let Some(key) = api_key {
            headers.insert("Authorization".to_string(), format!("Bearer {}", key));
        }
        Self {
            endpoint: endpoint.to_string(),
            protocol: OtelProtocol::Grpc,
            headers,
            service_name: "rush-workflow".to_string(),
            service_version: None,
            insecure: false,
            timeout_secs: 30,
        }
    }
}
