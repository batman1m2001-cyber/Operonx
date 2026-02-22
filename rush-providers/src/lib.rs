//! rush-providers — provider configs, native HTTP ops, and auth for Hush.
//!
//! Mirrors hush-providers: separate modules per concern.
//!
//! Architecture:
//!   config/   — Config structs parsed from Python (LLM, Embedding, Reranking)
//!   auth/     — Token providers (Keycloak, Google service account)
//!   http/     — Low-level HTTP clients (OpenAI, Azure, Gemini, TEI, Pinecone, Cohere)
//!   batch/    — OpenAI Batch API coordinator
//!   ops/      — High-level op implementations (load balancing, fallback, dispatch)
//!   py_serde  — PyObject ↔ serde_json::Value conversion utilities

pub mod auth;
pub mod batch;
pub mod config;
pub mod http;
pub mod ops;
pub mod py_serde;
