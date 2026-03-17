//! Example Rust ops server for Hush workflows.
//!
//! Demonstrates building a custom binary with hush-serve as a library.
//! All ops are compiled in — no cdylib/FFI plugin needed.
//!
//! ## Build & Run
//!
//! ```bash
//! cd examples/rust_ops && cargo build --release
//! ```
//!
//! ## Project structure convention
//!
//! ```text
//! my-project/
//! ├── pyproject.toml              # Python workflows
//! ├── workflows/
//! │   ├── pipeline.py
//! │   └── agent.py
//! ├── rust_ops/                   # ONE crate for all custom Rust ops
//! │   ├── Cargo.toml
//! │   └── src/
//! │       ├── main.rs             # Server entry point (uses hush-serve lib)
//! │       ├── math.rs             # Domain: math operations
//! │       ├── text.rs             # Domain: text processing
//! │       └── crypto.rs           # Domain: CPU-heavy crypto
//! ```

mod analytics;
mod crypto;
mod iteration;
mod math;
mod pipeline;
mod search;
mod streaming;
mod text;

fn main() {
    init_logging();

    if let Err(e) = hush_serve::HushServer::builder()
        .auto_register()
        .from_cli()
        .serve()
    {
        eprintln!("Error: {}", e);
        std::process::exit(1);
    }
}

fn init_logging() {
    if std::env::var("RUST_LOG").is_ok() {
        env_logger::init();
    } else {
        let filter = match std::env::var("LOG_LEVEL") {
            Ok(level) => match level.to_uppercase().as_str() {
                "DEBUG" => "hush=debug",
                "INFO" => "hush=info",
                "WARNING" | "WARN" => "hush=warn",
                "ERROR" | "CRITICAL" => "hush=error",
                _ => "hush=warn",
            }
            .to_string(),
            Err(_) => "hush=warn".to_string(),
        };
        env_logger::Builder::new().parse_filters(&filter).init();
    }
}
