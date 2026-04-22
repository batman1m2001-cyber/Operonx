//! Shared HTTP client singleton + [`ProviderError`].
//!
//! Rust-internal utility — no Python counterpart. Every HTTP-based provider
//! (OpenAI / Azure / Gemini / Anthropic / vLLM / Cohere / Pinecone / TEI /
//! Triton / Keycloak) uses the pooled [`reqwest::Client`] returned by
//! [`get_client`]. One pool per process keeps DNS + TLS + keep-alive warm.
//!
//! [`ProviderError`] adapts `reqwest::Error` into a typed category that
//! converts cleanly into [`OperonError::Provider`] at the trait boundary.

use std::sync::OnceLock;
use std::time::Duration;

use reqwest::Client;

use crate::core::exceptions::OperonError;

static HTTP_CLIENT: OnceLock<Client> = OnceLock::new();

/// Get (or lazily build) the shared pooled HTTP client.
///
/// Defaults — 10s connect timeout, 120s read timeout (LLM streams run long),
/// pool idle 10 per host.
pub fn get_client() -> &'static Client {
    HTTP_CLIENT.get_or_init(|| {
        Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(120))
            .pool_max_idle_per_host(10)
            .build()
            .expect("failed to build shared HTTP client")
    })
}

/// Typed error for HTTP-backed providers.
///
/// Carries the remote status code + a message. Any op-level path converts
/// this into [`OperonError::Provider`] at the trait boundary.
#[derive(Debug, Clone)]
pub struct ProviderError {
    pub message: String,
    pub status_code: Option<u16>,
    pub error_code: Option<i64>,
}

impl ProviderError {
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            status_code: None,
            error_code: None,
        }
    }

    pub fn with_status(mut self, status: u16) -> Self {
        self.status_code = Some(status);
        self
    }
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
        let kind = if e.is_timeout() {
            "request timed out"
        } else if e.is_connect() {
            "failed to connect"
        } else {
            "HTTP error"
        };
        ProviderError {
            message: format!("{}: {}", kind, e),
            status_code,
            error_code: None,
        }
    }
}

impl From<ProviderError> for OperonError {
    fn from(e: ProviderError) -> Self {
        OperonError::Provider(e.to_string())
    }
}

/// Convenience alias — every provider HTTP op returns this.
pub type ProviderResult<T> = Result<T, ProviderError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn provider_error_display_with_status() {
        let err = ProviderError::new("Not found").with_status(404);
        assert_eq!(err.to_string(), "[HTTP 404] Not found");
    }

    #[test]
    fn provider_error_flows_into_operon_error() {
        let err: OperonError = ProviderError::new("boom").with_status(500).into();
        assert!(matches!(err, OperonError::Provider(_)));
        assert!(err.to_string().contains("500"));
    }

    #[test]
    fn get_client_returns_stable_singleton() {
        let a = get_client();
        let b = get_client();
        assert!(std::ptr::eq(a, b));
    }
}
