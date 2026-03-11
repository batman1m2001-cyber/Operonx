//! rush-serve — standalone Rust HTTP server for Hush workflows.
//!
//! Takes a JSON config file (produced by `hush-serve` Python bridge),
//! parses workflow definitions, and serves them via Axum.
//! Pure Rust — no Python, no PyO3.
//!
//! Usage:
//!   rush-serve --config /path/to/config.json
//!   rush-serve --config /path/to/config.json --host 0.0.0.0 --port 8080

mod config;
mod error;
mod execute;
mod router;
mod routes;
mod state;

use clap::Parser;
use config::{Cli, ServerConfig};
use std::net::SocketAddr;

#[tokio::main]
async fn main() {
    env_logger::init();

    let cli = Cli::parse();

    // Read config file
    let config_str = std::fs::read_to_string(&cli.config).unwrap_or_else(|e| {
        eprintln!("Failed to read config file '{}': {}", cli.config, e);
        std::process::exit(1);
    });

    let mut server_config: ServerConfig = serde_json::from_str(&config_str).unwrap_or_else(|e| {
        eprintln!("Failed to parse config: {}", e);
        std::process::exit(1);
    });

    // CLI overrides
    if let Some(host) = cli.host {
        server_config.host = host;
    }
    if let Some(port) = cli.port {
        server_config.port = port;
    }

    let addr: SocketAddr = format!("{}:{}", server_config.host, server_config.port)
        .parse()
        .unwrap_or_else(|e| {
            eprintln!("Invalid address: {}", e);
            std::process::exit(1);
        });

    let n_endpoints = server_config.endpoints.len();
    let app = router::build_router(server_config);

    println!("rush-serve running at http://{}", addr);
    println!("Endpoints: {}", n_endpoints);

    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .unwrap_or_else(|e| {
            eprintln!("Failed to bind to {}: {}", addr, e);
            std::process::exit(1);
        });

    axum::serve(listener, app).await.unwrap_or_else(|e| {
        eprintln!("Server error: {}", e);
        std::process::exit(1);
    });
}
