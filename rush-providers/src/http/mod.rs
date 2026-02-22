//! Shared HTTP client and provider-specific HTTP modules.
//!
//! Provides a connection-pooled reqwest Client singleton reused across all
//! provider operations. Connection pools are shared for better performance.

pub mod embedding;
pub mod llm;
pub mod reranker;

use std::sync::OnceLock;
use std::time::Duration;

use reqwest::Client;

static HTTP_CLIENT: OnceLock<Client> = OnceLock::new();

/// Get the shared HTTP client with connection pooling.
///
/// Configured with:
/// - 10s connect timeout
/// - 120s read timeout (LLM streaming can be slow)
/// - Connection pooling (reqwest default: pool idle timeout 90s)
/// - rustls TLS backend (no OpenSSL dependency)
pub fn get_client() -> &'static Client {
    HTTP_CLIENT.get_or_init(|| {
        Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(120))
            .pool_max_idle_per_host(10)
            .build()
            .expect("Failed to create HTTP client")
    })
}

/// Common error type for HTTP provider operations.
#[derive(Debug)]
pub struct ProviderError {
    pub message: String,
    pub status_code: Option<u16>,
    pub error_code: Option<i64>,
}

impl std::fmt::Display for ProviderError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if let Some(code) = self.status_code {
            write!(f, "[HTTP {}] {}", code, self.message)
        } else {
            write!(f, "{}", self.message)
        }
    }
}

impl std::error::Error for ProviderError {}

impl From<reqwest::Error> for ProviderError {
    fn from(e: reqwest::Error) -> Self {
        let status_code = e.status().map(|s| s.as_u16());
        if e.is_timeout() {
            ProviderError {
                message: format!("Request timed out: {}", e),
                status_code,
                error_code: None,
            }
        } else if e.is_connect() {
            ProviderError {
                message: format!("Failed to connect: {}", e),
                status_code,
                error_code: None,
            }
        } else {
            ProviderError {
                message: format!("HTTP error: {}", e),
                status_code,
                error_code: None,
            }
        }
    }
}

pub type ProviderResult<T> = Result<T, ProviderError>;
