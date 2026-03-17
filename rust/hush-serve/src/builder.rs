//! Server builder — fluent API for configuring and running a Hush HTTP server.
//!
//! # Example
//!
//! ```rust,ignore
//! use hush_serve::HushServer;
//! use serde_json::{json, Value};
//!
//! fn main() {
//!     HushServer::builder()
//!         .register_op("double", |v: &Value| {
//!             let x = v["x"].as_i64().unwrap_or(0);
//!             json!({"result": x * 2})
//!         })
//!         .from_cli()
//!         .serve()
//!         .unwrap();
//! }
//! ```

use std::any::{Any, TypeId};
use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;

use serde_json::Value;

use hush_icore::registry::OpRegistry;

use crate::config::{Cli, ServerConfig};
use crate::fn_registry::{CompositeRegistry, FnRegistry};
use crate::resources;

/// Builder for configuring and running a Hush HTTP server.
pub struct HushServerBuilder {
    registry: FnRegistry,
    config: Option<ServerConfig>,
    host_override: Option<String>,
    port_override: Option<u16>,
    pending_resources: HashMap<TypeId, Arc<dyn Any + Send + Sync>>,
}

impl HushServerBuilder {
    pub fn new() -> Self {
        HushServerBuilder {
            registry: FnRegistry::new(),
            config: None,
            host_override: None,
            port_override: None,
            pending_resources: HashMap::new(),
        }
    }

    /// Auto-register all `#[hush_op]` ops and `#[hush_resource]` factories.
    ///
    /// Discovers ops and resources registered via `inventory` at link time.
    /// Resources are constructed from the `resources` section of the JSON config.
    /// Call `.from_cli()` or `.config_file()` before this to load the config first.
    pub fn auto_register(mut self) -> Self {
        self.registry.collect_from_inventory();

        // Collect #[hush_resource] factories and construct from config
        let resources_config = self.config.as_ref()
            .and_then(|c| c.resources.clone())
            .unwrap_or_default();

        for entry in inventory::iter::<crate::ResourceEntry> {
            let config = resources_config.get(entry.name)
                .cloned()
                .unwrap_or(serde_json::Value::Object(serde_json::Map::new()));

            let resource: Box<dyn Any + Send + Sync> = (entry.factory)(&config);
            let type_id = (*resource).type_id();
            self.pending_resources.insert(type_id, Arc::from(resource));

            println!("Auto-provide resource: {}", entry.name);
        }

        self
    }

    /// Provide a typed resource accessible by `hush_serve::get::<T>()` in ops.
    ///
    /// Resources must be `Send + Sync + 'static`. Multiple resources of different
    /// types can be provided. Only one resource per type is allowed.
    ///
    /// # Example
    ///
    /// ```rust,ignore
    /// HushServer::builder()
    ///     .provide(OnnxEmbedder::new("./models/bge-m3").unwrap())
    ///     .provide(SentimentModels::load())
    ///     .auto_register()
    ///     .from_cli()
    ///     .serve()
    /// ```
    pub fn provide<T: Send + Sync + 'static>(mut self, resource: T) -> Self {
        self.pending_resources
            .insert(TypeId::of::<T>(), Arc::new(resource));
        self
    }

    /// Register a regular op by name.
    ///
    /// The closure receives `&serde_json::Value` (op inputs) and returns `Value` (op outputs).
    /// Closures can capture shared state via `Arc`:
    ///
    /// ```rust,ignore
    /// let pool = Arc::new(MyPool::new());
    /// let p = pool.clone();
    /// builder.register_op("my_op", move |inputs| {
    ///     p.do_work(inputs)
    /// })
    /// ```
    pub fn register_op<F>(mut self, name: &str, f: F) -> Self
    where
        F: Fn(&Value) -> Value + Send + Sync + 'static,
    {
        self.registry.register_op(name, f);
        self
    }

    /// Register a generator op by name (returns `Vec<Value>`).
    ///
    /// The closure returns `Vec<Value>` — one entry per yield.
    pub fn register_generator<F>(mut self, name: &str, f: F) -> Self
    where
        F: Fn(&Value) -> Vec<Value> + Send + Sync + 'static,
    {
        self.registry.register_generator(name, f);
        self
    }

    /// Register a generator op that returns a JSON array `Value`.
    ///
    /// This matches the cdylib convention where generators return `Value::Array`.
    /// The array is unwrapped automatically.
    pub fn register_generator_value<F>(mut self, name: &str, f: F) -> Self
    where
        F: Fn(&Value) -> Value + Send + Sync + 'static,
    {
        self.registry.register_generator_value(name, f);
        self
    }

    /// Load server config from a JSON file.
    pub fn config_file(mut self, path: &str) -> Self {
        let config_str = std::fs::read_to_string(path).unwrap_or_else(|e| {
            eprintln!("Failed to read config file '{}': {}", path, e);
            std::process::exit(1);
        });
        let config: ServerConfig = serde_json::from_str(&config_str).unwrap_or_else(|e| {
            eprintln!("Failed to parse config: {}", e);
            std::process::exit(1);
        });
        self.config = Some(config);
        self
    }

    /// Load server config from a JSON string.
    pub fn config_json(mut self, json: &str) -> Self {
        let config: ServerConfig = serde_json::from_str(json).unwrap_or_else(|e| {
            eprintln!("Failed to parse config: {}", e);
            std::process::exit(1);
        });
        self.config = Some(config);
        self
    }

    /// Parse CLI arguments: `--config`, `--host`, `--port`.
    ///
    /// This is the standard entry point for binaries spawned by the Python bridge.
    pub fn from_cli(mut self) -> Self {
        use clap::Parser;
        let cli = Cli::parse();

        // Read config file
        let config_str = std::fs::read_to_string(&cli.config).unwrap_or_else(|e| {
            eprintln!("Failed to read config file '{}': {}", cli.config, e);
            std::process::exit(1);
        });
        let config: ServerConfig = serde_json::from_str(&config_str).unwrap_or_else(|e| {
            eprintln!("Failed to parse config: {}", e);
            std::process::exit(1);
        });
        self.config = Some(config);

        // CLI overrides
        if let Some(host) = cli.host {
            self.host_override = Some(host);
        }
        if let Some(port) = cli.port {
            self.port_override = Some(port);
        }
        // plugin field ignored (legacy — cdylib plugins removed)

        self
    }

    /// Override the bind host.
    pub fn host(mut self, h: &str) -> Self {
        self.host_override = Some(h.to_string());
        self
    }

    /// Override the bind port.
    pub fn port(mut self, p: u16) -> Self {
        self.port_override = Some(p);
        self
    }

    /// Build and run the HTTP server (blocking).
    ///
    /// This starts a Tokio runtime, binds the server, and blocks until shutdown.
    pub fn serve(self) -> Result<(), Box<dyn std::error::Error>> {
        let rt = tokio::runtime::Runtime::new()?;
        rt.block_on(self.serve_async())
    }

    /// Build and run the HTTP server (async).
    pub async fn serve_async(self) -> Result<(), Box<dyn std::error::Error>> {
        // Initialize typed resources before serving
        if !self.pending_resources.is_empty() {
            resources::init_resources(self.pending_resources);
        }

        // Register provider op factory — enables hush-icore to dispatch
        // LLM, embedding, rerank, onnx, prompt, parser ops to hush-providers.
        hush_icore::ops::op_trait::set_global_factory(
            Arc::new(hush_providers::ops::factory::ProviderOpFactory),
        );

        let mut config = self.config.ok_or("No config provided. Use .config_file(), .config_json(), or .from_cli()")?;

        // Apply overrides
        if let Some(host) = self.host_override {
            config.host = host;
        }
        if let Some(port) = self.port_override {
            config.port = port;
        }

        // Build registry from auto-registered #[hush_op] functions
        let registry: Option<Arc<dyn OpRegistry>> = if !self.registry.is_empty() {
            Some(Arc::new(self.registry))
        } else {
            None
        };

        let addr: SocketAddr = format!("{}:{}", config.host, config.port).parse()?;
        let n_endpoints = config.endpoints.len();
        let app = crate::router::build_router(config, registry);

        println!("hush-serve running at http://{}", addr);
        println!("Endpoints: {}", n_endpoints);

        let listener = tokio::net::TcpListener::bind(addr).await?;

        axum::serve(listener, app)
            .with_graceful_shutdown(shutdown_signal())
            .await?;

        println!("hush-serve shut down.");
        Ok(())
    }

    /// Build the Axum Router without starting the server (for testing or embedding).
    pub fn into_router(self) -> Result<axum::Router, Box<dyn std::error::Error>> {
        let config = self.config.ok_or("No config provided")?;

        let has_fn_ops = !self.registry.is_empty();
        let registry: Option<Arc<dyn OpRegistry>> = if has_fn_ops {
            Some(Arc::new(self.registry))
        } else {
            None
        };

        Ok(crate::router::build_router(config, registry))
    }
}

impl Default for HushServerBuilder {
    fn default() -> Self {
        Self::new()
    }
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
