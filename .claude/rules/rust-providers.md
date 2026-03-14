---
paths: ["rust/hush-providers/**"]
---

# hush-providers (Rust)

Native HTTP providers and ONNX inference. Mirrors Python hush-providers.

## Module Structure

```
src/
├── config/             # LLMProviderConfig, EmbeddingConfig, RerankingConfig
├── auth/               # Keycloak, Google service account tokens
├── http/               # Shared reqwest Client singleton, ProviderError
├── llms/
│   ├── mod.rs          # Dispatch: chat_completion(), chat_completion_stream()
│   ├── types.rs        # ChatCompletionRequest/Response, build/format helpers
│   ├── openai.rs       # OpenAI + vLLM (shared API format)
│   ├── azure.rs        # Azure OpenAI
│   ├── gemini.rs       # Google Gemini
│   └── image.rs        # Multimodal: encode_image(), resolve_image_paths()
├── embeddings/
│   ├── openai.rs       # OpenAI / vLLM / Azure
│   ├── onnx.rs         # ONNX via ort (pure Rust)
│   └── huggingface.rs  # Stub — use ONNX instead
├── rerankers/
│   ├── vllm.rs, pinecone.rs, cohere.rs
│   ├── onnx.rs         # ONNX cross-encoder
│   └── huggingface.rs  # Stub — use ONNX instead
├── ops/                # execute() dispatch, LLM/Embedding/Rerank/Prompt/Parser/Chain ops
└── batch/              # OpenAI Batch API coordinator
```

## Provider Types

| Type | Native HTTP | Pure Rust (ONNX) |
|------|-------------|------------------|
| LLM | OpenAI, Azure, Gemini | — |
| Embedding | OpenAI/vLLM | ONNX (via ort) |
| Reranker | vLLM, Pinecone, Cohere | ONNX (via ort) |

## Dispatch Pattern

```rust
pub async fn chat_completion(config: &LLMConfig, inputs: &Value, token: Option<&str>) -> ProviderResult<Value> {
    match config {
        LLMConfig::OpenAI(c) => openai::chat_completion(c, &inputs, token).await,
        LLMConfig::Azure(c)  => azure::chat_completion(c, &inputs).await,
        LLMConfig::Gemini(c) => gemini::chat_completion(c, &inputs, token).await,
    }
}
```

## Adding a New Provider

1. Create `src/{llms,embeddings,rerankers}/new_provider.rs`
2. Add dispatch case in category's `mod.rs`
3. Add config variant in `config/{llm,embedding,reranking}.rs`
4. Update `is_native_provider_op()` in `config/mod.rs` and `ops/mod.rs`
5. Add `#[cfg(test)]` tests
