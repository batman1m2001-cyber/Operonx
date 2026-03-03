//! CLI and server configuration.

use clap::Parser;
use serde::Deserialize;

/// CLI arguments for the rush-serve binary.
#[derive(Parser)]
#[command(name = "rush-serve", about = "Standalone Rust HTTP server for Hush workflows")]
pub struct Cli {
    /// Path to the JSON config file (produced by hush-serve Python bridge).
    #[arg(long, env = "RUSH_SERVE_CONFIG")]
    pub config: String,

    /// Bind host (overrides config file).
    #[arg(long, env = "RUSH_SERVE_HOST")]
    pub host: Option<String>,

    /// Bind port (overrides config file).
    #[arg(long, env = "RUSH_SERVE_PORT")]
    pub port: Option<u16>,
}

/// Top-level server config, deserialized from JSON.
#[derive(Debug, Deserialize)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
    pub endpoints: Vec<EndpointDef>,
}

/// A single endpoint definition.
#[derive(Debug, Deserialize)]
pub struct EndpointDef {
    pub path: String,
    /// Serialized GraphOp config (output of `GraphOp.serialize()`).
    pub graph: serde_json::Value,
    #[serde(default)]
    pub stream: Option<bool>,
    #[serde(default = "default_true")]
    pub batch: bool,
    #[serde(default)]
    pub websocket: bool,
    #[serde(default)]
    pub jobs: bool,
    #[serde(default = "default_batch_size")]
    pub max_batch_size: usize,
    #[serde(default = "default_batch_concurrency")]
    pub batch_concurrency: usize,
}

fn default_true() -> bool {
    true
}

fn default_batch_size() -> usize {
    100
}

fn default_batch_concurrency() -> usize {
    10
}
