# rush-providers

Rust provider implementations for Hush workflows. Per-provider module architecture mirroring hush-providers. Native HTTP for cloud providers, ONNX via `ort` crate.

## Module Structure

```
rush-providers/src/
├── lib.rs              # Module declarations
├── config/             # Config structs parsed from JSON
│   ├── mod.rs          # LLMProviderConfig enum, is_native_provider_op()
│   ├── llm.rs          # LLMConfig (OpenAI, Azure, Gemini variants)
│   ├── embedding.rs    # EmbeddingConfig
│   └── reranking.rs    # RerankingConfig
├── auth/               # Token providers (Keycloak, Google service account)
├── http/               # Shared reqwest Client + ProviderError
│   └── mod.rs          # get_client() singleton, ProviderError, ProviderResult
├── llms/               # LLM providers
│   ├── mod.rs          # Dispatch: chat_completion(), chat_completion_stream()
│   ├── types.rs        # ChatCompletionRequest/Response, build/format helpers
│   ├── openai.rs       # OpenAI + vLLM (shared API format)
│   ├── azure.rs        # Azure OpenAI
│   ├── gemini.rs       # Google Gemini
│   └── image.rs        # Multimodal: encode_image(), resolve_image_paths()
├── embeddings/         # Embedding providers
│   ├── mod.rs          # Dispatch: embed()
│   ├── openai.rs       # OpenAI / vLLM / Azure embeddings
│   ├── huggingface.rs  # HuggingFace stub (PyO3 removed, use ONNX instead)
│   └── onnx.rs         # ONNX via ort crate (pure Rust)
├── rerankers/          # Reranker providers
│   ├── mod.rs          # Dispatch: rerank() + shared filter_and_sort()
│   ├── vllm.rs         # vLLM reranker
│   ├── pinecone.rs     # Pinecone reranker
│   ├── cohere.rs       # Cohere reranker
│   ├── huggingface.rs  # HuggingFace stub (PyO3 removed, use ONNX instead)
│   └── onnx.rs         # ONNX cross-encoder (pure Rust via ort)
├── batch/              # OpenAI Batch API coordinator
└── ops/                # High-level op implementations
    ├── mod.rs          # execute() dispatch + is_native_provider_op()
    ├── llm.rs          # LLMOp: select_backend → llms::chat_completion
    ├── embedding.rs    # EmbeddingOp: embeddings::embed
    ├── rerank.rs       # RerankOp: rerankers::rerank
    ├── prompt.rs       # PromptOp: template formatting (string/dict/list) + missing var detection
    ├── parser.rs       # ParserOp: extract structured data (JSON/XML/YAML)
    └── chain.rs        # ChainOp: prompt + LLM + optional parser (extract mode)
```

## Key Files to Read First

1. `src/ops/mod.rs` — Op dispatch: `execute()` routes by op type, `is_native_provider_op()` checks if Rust handles this provider
2. `src/llms/mod.rs` — LLM dispatch by config variant (OpenAI/Azure/Gemini)
3. `src/llms/types.rs` — Shared request/response types, `build_chat_request()`, `format_completion_response()`
4. `src/config/mod.rs` — Config parsing from Python dicts

## Provider Architecture

### Per-Provider Modules

Each provider category (llms/, embeddings/, rerankers/) follows the same pattern:
- **`mod.rs`**: Public dispatch function that routes by config variant/api_type
- **Per-provider file**: Implements the actual HTTP call or inference
- **Shared types**: Common request/response structs (e.g., `llms/types.rs`)

### Provider Types

| Type | Native HTTP | Pure Rust (ONNX) |
|------|-------------|------------------|
| **LLM** | OpenAI, Azure, Gemini | - |
| **Embedding** | OpenAI/vLLM | ONNX (via ort) |
| **Reranker** | vLLM, Pinecone, Cohere | ONNX (via ort) |

- **Native HTTP**: Uses `reqwest` for direct API calls (fastest)
- **Pure Rust**: Uses `ort` crate for ONNX Runtime inference (no Python needed)
- **HuggingFace**: Stubs only — use ONNX export or Python backend (hush-providers)

### Dispatch Pattern

```rust
// llms/mod.rs
pub async fn chat_completion(config: &LLMConfig, inputs: &Value, token: Option<&str>) -> ProviderResult<Value> {
    image::resolve_image_paths(&mut inputs);  // multimodal support
    match config {
        LLMConfig::OpenAI(c) => openai::chat_completion(c, &inputs, token).await,
        LLMConfig::Azure(c)  => azure::chat_completion(c, &inputs).await,
        LLMConfig::Gemini(c) => gemini::chat_completion(c, &inputs, token).await,
    }
}
```

## Dependencies

| Crate | Purpose |
|-------|---------|
| `reqwest 0.12` | HTTP client for cloud provider APIs |
| `serde / serde_json` | JSON serialization |
| `ort 2` | ONNX Runtime for pure Rust inference (optional) |
| `tokenizers 0.20` | HuggingFace tokenizers for ONNX models (optional) |
| `base64 0.22` | Image encoding for multimodal LLMs |
| `tokio 1` | Async runtime |
| `tempfile 3` | Dev dependency for tests |

## Build & Test

```bash
# Build (as part of rush-core)
cargo build --release

# Run Rust tests
cargo test -p rush-providers
```

## Adding a New Provider

1. Create `src/{llms,embeddings,rerankers}/new_provider.rs`
2. Add dispatch case in the category's `mod.rs`
3. Add config variant in `config/{llm,embedding,reranking}.rs`
4. Update `is_native_provider_op()` in `config/mod.rs` and `ops/mod.rs`
5. Add unit tests with `#[cfg(test)] mod tests { ... }`

## Deep Documentation Links

| Topic | File |
|-------|------|
| Provider abstractions | [docs/architecture/providers/adding-new-provider.md](../../docs/architecture/providers/adding-new-provider.md) |
| Workflow ops design | [docs/architecture/providers/workflow-ops.md](../../docs/architecture/providers/workflow-ops.md) |
| Rust backend overview | [rush-core/CLAUDE.md](../rush-core/CLAUDE.md) |
