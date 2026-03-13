//! CLI and server configuration.

use clap::Parser;
use serde::Deserialize;

/// CLI arguments for the hush-serve binary.
#[derive(Parser)]
#[command(name = "hush-serve", about = "Standalone Rust HTTP server for Hush workflows")]
pub struct Cli {
    /// Path to the JSON config file (produced by hush-serve Python bridge).
    #[arg(long, env = "HUSH_SERVE_CONFIG")]
    pub config: String,

    /// Bind host (overrides config file).
    #[arg(long, env = "HUSH_SERVE_HOST")]
    pub host: Option<String>,

    /// Bind port (overrides config file).
    #[arg(long, env = "HUSH_SERVE_PORT")]
    pub port: Option<u16>,

    /// Path to a cdylib plugin (.dll/.so/.dylib) providing custom Rust ops.
    #[arg(long, env = "HUSH_SERVE_PLUGIN")]
    pub plugin: Option<String>,
}

/// Tracer configuration section.
#[derive(Debug, Deserialize, Clone, Default)]
pub struct TracerConfig {
    /// Enable HushEyes tracer (local trace visualization).
    #[serde(default)]
    pub hush_eyes: Option<HushEyesConfig>,
    /// Enable Langfuse tracer.
    #[serde(default)]
    pub langfuse: Option<LangfuseTracerConfig>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct HushEyesConfig {
    #[serde(default = "default_hush_eyes_host")]
    pub host: String,
    #[serde(default = "default_hush_eyes_port")]
    pub port: u16,
}

fn default_hush_eyes_host() -> String {
    "127.0.0.1".to_string()
}

fn default_hush_eyes_port() -> u16 {
    8420
}

#[derive(Debug, Deserialize, Clone)]
pub struct LangfuseTracerConfig {
    /// Langfuse public key (or set LANGFUSE_PUBLIC_KEY env var).
    pub public_key: Option<String>,
    /// Langfuse secret key (or set LANGFUSE_SECRET_KEY env var).
    pub secret_key: Option<String>,
    /// Langfuse host (default: cloud.langfuse.com).
    pub host: Option<String>,
    /// Stream trace limit per generator.
    pub stream_trace_limit: Option<usize>,
}

/// Top-level server config, deserialized from JSON.
#[derive(Debug, Deserialize)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
    pub endpoints: Vec<EndpointDef>,
    #[serde(default)]
    pub tracers: Option<TracerConfig>,
}

/// A single endpoint definition.
#[derive(Debug, Deserialize)]
pub struct EndpointDef {
    pub path: String,
    /// Serialized GraphOp config (output of `GraphOp.serialize()`).
    pub graph: serde_json::Value,
    #[serde(default)]
    pub stream: Option<bool>,
    #[serde(default)]
    pub websocket: bool,
}
