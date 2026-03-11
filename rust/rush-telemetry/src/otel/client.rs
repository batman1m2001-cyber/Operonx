//! OtelClient — OpenTelemetry SDK wrapper for trace export.
//!
//! Uses opentelemetry-rust crate to create spans and export to any
//! OTLP-compatible backend (Jaeger, Zipkin, Tempo, etc.).

use opentelemetry::trace::TracerProvider as _;
use opentelemetry::KeyValue;
use opentelemetry_sdk::trace::SdkTracerProvider;
use opentelemetry_sdk::Resource;

use super::config::{OtelConfig, OtelProtocol};

/// OpenTelemetry client wrapping the SDK tracer provider.
pub struct OtelClient {
    provider: SdkTracerProvider,
    tracer: opentelemetry_sdk::trace::Tracer,
}

impl std::fmt::Debug for OtelClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "<OtelClient>")
    }
}

impl OtelClient {
    /// Create a new OtelClient from config.
    ///
    /// Initializes the OTLP exporter and tracer provider.
    pub fn new(config: &OtelConfig) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let mut resource_attrs = vec![KeyValue::new(
            "service.name",
            config.service_name.clone(),
        )];
        if let Some(ref ver) = config.service_version {
            resource_attrs.push(KeyValue::new("service.version", ver.clone()));
        }
        let resource = Resource::builder().with_attributes(resource_attrs).build();

        let exporter = Self::create_exporter(config)?;

        let provider = SdkTracerProvider::builder()
            .with_resource(resource)
            .with_batch_exporter(exporter)
            .build();

        let tracer = provider.tracer(config.service_name.clone());

        Ok(Self { provider, tracer })
    }

    fn create_exporter(
        config: &OtelConfig,
    ) -> Result<opentelemetry_otlp::SpanExporter, Box<dyn std::error::Error + Send + Sync>> {
        use opentelemetry_otlp::WithExportConfig;

        match config.protocol {
            OtelProtocol::Grpc => {
                let exporter = opentelemetry_otlp::SpanExporter::builder()
                    .with_tonic()
                    .with_endpoint(&config.endpoint)
                    .build()?;
                Ok(exporter)
            }
            OtelProtocol::Http => {
                let exporter = opentelemetry_otlp::SpanExporter::builder()
                    .with_http()
                    .with_endpoint(&config.endpoint)
                    .build()?;
                Ok(exporter)
            }
        }
    }

    /// Get the underlying tracer for creating spans.
    pub fn tracer(&self) -> &opentelemetry_sdk::trace::Tracer {
        &self.tracer
    }

    /// Flush all pending spans.
    pub fn flush(&self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        self.provider.force_flush()?;
        Ok(())
    }

    /// Shutdown the tracer provider.
    pub fn shutdown(self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        self.provider.shutdown()?;
        Ok(())
    }
}
