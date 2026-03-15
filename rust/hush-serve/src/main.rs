//! hush-serve — default binary for serving Hush workflows.
//!
//! Usage:
//!   hush-serve --config /path/to/config.json
//!   hush-serve --config /path/to/config.json --host 0.0.0.0 --port 8080
//!   hush-serve --config /path/to/config.json --plugin /path/to/plugin.so

use hush_serve::HushServer;

fn main() {
    init_logging();

    if let Err(e) = HushServer::builder().from_cli().serve() {
        eprintln!("Error: {}", e);
        std::process::exit(1);
    }
}

/// Initialize logging: respect RUST_LOG if set, otherwise read LOG_LEVEL.
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
