//! hush-serve — standalone Rust HTTP server for Hush workflows.
//!
//! Takes a JSON config file (produced by `hush-serve` Python bridge),
//! parses workflow definitions, and serves them via Axum.
//! Pure Rust — no Python, no PyO3.
//!
//! Usage:
//!   hush-serve --config /path/to/config.json
//!   hush-serve --config /path/to/config.json --host 0.0.0.0 --port 8080

mod config;
mod error;
mod execute;
mod plugin;
mod router;
mod routes;
mod state;

use std::net::SocketAddr;
use std::sync::Arc;

use clap::Parser;
use config::{Cli, ServerConfig};
use hush_icore::registry::OpRegistry;

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

    // Load plugin if specified
    let registry: Option<Arc<dyn OpRegistry>> = if let Some(ref plugin_path) = cli.plugin {
        let path = std::path::Path::new(plugin_path);
        if !path.exists() {
            eprintln!("Plugin file not found: {}", plugin_path);
            std::process::exit(1);
        }
        let plugin = plugin::PluginRegistry::load(path).unwrap_or_else(|e| {
            eprintln!("{}", e);
            std::process::exit(1);
        });
        println!("Loaded plugin: {}", plugin_path);
        Some(Arc::new(plugin))
    } else {
        None
    };

    let addr: SocketAddr = format!("{}:{}", server_config.host, server_config.port)
        .parse()
        .unwrap_or_else(|e| {
            eprintln!("Invalid address: {}", e);
            std::process::exit(1);
        });

    let n_endpoints = server_config.endpoints.len();
    let app = router::build_router(server_config, registry);

    println!("hush-serve running at http://{}", addr);
    println!("Endpoints: {}", n_endpoints);

    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .unwrap_or_else(|e| {
            eprintln!("Failed to bind to {}: {}", addr, e);
            std::process::exit(1);
        });

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .unwrap_or_else(|e| {
            eprintln!("Server error: {}", e);
            std::process::exit(1);
        });

    println!("hush-serve shut down.");
}

/// Wait for Ctrl+C or SIGTERM to trigger graceful shutdown.
async fn shutdown_signal() {
    let ctrl_c = tokio::signal::ctrl_c();

    #[cfg(unix)]
    {
        let mut sigterm =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).unwrap();
        tokio::select! {
            _ = ctrl_c => { println!("\nReceived Ctrl+C, shutting down..."); }
            _ = sigterm.recv() => { println!("\nReceived SIGTERM, shutting down..."); }
        }
    }

    #[cfg(not(unix))]
    {
        ctrl_c.await.ok();
        println!("\nReceived Ctrl+C, shutting down...");
    }
}
