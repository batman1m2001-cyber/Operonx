//! Authentication providers — Keycloak and Google service account.
//!
//! Manages token lifecycle: fetch, cache, background refresh.
//! Thread-safe via tokio Mutex and OnceLock.

pub mod google;
pub mod keycloak;
