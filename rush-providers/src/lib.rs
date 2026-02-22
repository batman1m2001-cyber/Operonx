//! rush-providers — provider configs, native ops, and auth for Hush.
//!
//! Mirrors hush-providers: separate modules per concern.
//!
//! Architecture:
//!   config/      — Config structs parsed from Python (LLM, Embedding, Reranking)
//!   auth/        — Token providers (Keycloak, Google service account)
//!   http/        — Shared HTTP client + error types
//!   llms/        — LLM providers (OpenAI, Azure, Gemini) + image/multimodal
//!   embeddings/  — Embedding providers (OpenAI/vLLM, HF via PyO3, ONNX via ort)
//!   rerankers/   — Reranker providers (vLLM, Pinecone, Cohere, HF via PyO3, ONNX via ort)
//!   batch/       — OpenAI Batch API coordinator
//!   ops/         — High-level op implementations (load balancing, fallback, chain, dispatch)
//!   py_serde     — PyObject ↔ serde_json::Value conversion utilities

pub mod auth;
pub mod batch;
pub mod config;
pub mod embeddings;
pub mod http;
pub mod llms;
pub mod ops;
pub mod py_serde;
pub mod rerankers;
