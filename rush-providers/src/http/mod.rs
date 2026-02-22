//! Shared HTTP client and error types.
//!
//! Provides a connection-pooled reqwest Client singleton reused across all
//! provider operations. Connection pools are shared for better performance.
//!
//! Provider-specific HTTP logic has moved to:
//! - llms/ — LLM providers (OpenAI, Azure, Gemini)
//! - embeddings/ — Embedding providers (OpenAI/vLLM)
//! - rerankers/ — Reranker providers (vLLM, Pinecone, Cohere)

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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_provider_error_display_with_status() {
        let err = ProviderError {
            message: "Not found".to_string(),
            status_code: Some(404),
            error_code: None,
        };
        let display = format!("{}", err);
        assert_eq!(display, "[HTTP 404] Not found");
    }

    #[test]
    fn test_provider_error_display_without_status() {
        let err = ProviderError {
            message: "Something went wrong".to_string(),
            status_code: None,
            error_code: None,
        };
        let display = format!("{}", err);
        assert_eq!(display, "Something went wrong");
    }

    #[test]
    fn test_provider_error_debug() {
        let err = ProviderError {
            message: "test".to_string(),
            status_code: Some(500),
            error_code: Some(42),
        };
        let debug = format!("{:?}", err);
        assert!(debug.contains("test"));
        assert!(debug.contains("500"));
        assert!(debug.contains("42"));
    }

    #[test]
    fn test_get_client_returns_same_instance() {
        let client1 = get_client();
        let client2 = get_client();
        // Both should be the same static reference
        assert!(std::ptr::eq(client1, client2));
    }
}
