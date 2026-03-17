//! hush-providers — provider configs, native ops, and auth for Hush.
//!
//! Pure Rust provider implementations — no PyO3 dependency.
//!
//! Architecture:
//!   config/      — Config structs parsed from JSON
//!   auth/        — Token providers (Keycloak, Google service account)
//!   http/        — Shared HTTP client + error types
//!   llms/        — LLM providers (OpenAI, Azure, Gemini) + image/multimodal
//!   embeddings/  — Embedding providers (OpenAI/vLLM, ONNX via ort)
//!   rerankers/   — Reranker providers (vLLM, Pinecone, Cohere, ONNX via ort)
//!   batch/       — OpenAI Batch API coordinator
//!   ops/         — High-level op implementations (load balancing, fallback, chain, dispatch)

pub mod auth;
pub mod batch;
pub mod config;
pub mod embeddings;
pub mod http;
pub mod llms;
#[cfg(feature = "onnx")]
pub mod onnx;
pub mod ops;
pub mod rerankers;
