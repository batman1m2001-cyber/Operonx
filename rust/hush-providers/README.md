# hush-providers

Native Rust provider implementations for Hush workflows — HTTP providers + ONNX inference.

[![crates.io](https://img.shields.io/crates/v/hush-providers)](https://crates.io/crates/hush-providers)

## Supported Providers

| Type | Native HTTP | Pure Rust (ONNX) |
|------|-------------|------------------|
| **LLM** | OpenAI, Azure, Gemini, vLLM | — |
| **Embedding** | OpenAI/vLLM | ONNX (via `ort`) |
| **Reranker** | vLLM, Pinecone, Cohere | ONNX (via `ort`) |

## Architecture

- Direct `reqwest` HTTP calls to cloud APIs (no Python, no SDK overhead)
- ONNX Runtime inference via `ort` crate for local models
- Per-provider dispatch by config variant

## Usage

Used internally by [hush-icore](https://crates.io/crates/hush-icore) — not typically called directly.

## License

Apache 2.0
