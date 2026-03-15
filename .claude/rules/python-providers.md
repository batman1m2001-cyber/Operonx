---
paths: ["python/hush-providers/**"]
---

# hush-providers (Python)

LLM, embedding, and reranking provider integrations.

## Module Structure

```
hush/providers/
├── llms/               # BaseLLM, config, factory, openai, azure, gemini
├── embeddings/         # BaseEmbedder, config, factory, vllm, tei, hf, onnx
├── rerankers/          # BaseReranker, config, factory, vllm, tei, hf, onnx, pinecone
├── auth/               # Keycloak token provider
├── ops/                # LLMOp, ChainOp, EmbeddingOp, RerankOp, PromptOp
└── registry/           # Plugin registration (llm, embedding, rerank, auth)
```

## Provider Pattern

Each type: `base.py` (interface) + `config.py` (Pydantic) + `factory.py` + `{impl}.py`

## Adding a New Provider

1. Add config in `{type}/config.py`: `class MyConfig(LLMConfig): type = "my_provider"`
2. Create `{type}/my_provider.py`: inherit `BaseLLM`/`BaseEmbedder`/`BaseReranker`
3. Register in `{type}/factory.py`: `Factory.register("my_provider", MyImpl)`
4. Export in `{type}/__init__.py`

### Key interfaces
- `BaseLLM`: `stream()` → AsyncGenerator, `generate()` → ChatCompletion
- `BaseEmbedder`: `embed(texts)` → List[List[float]], `embed_batch()`
- `BaseReranker`: `rerank(query, docs, top_k)` → List[{index, score, text}]

## Workflow Ops

```python
# chain() = Prompt + LLM combined
chat = chain(resource="gpt-4o", template={"system": "...", "user": "{q}"}, q=PARENT["q"])

# Separate ops
llm = LLMOp.of(resource="gpt-4o", messages=PARENT["msgs"])
p = PromptOp.of(template={"system": "...", "user": "{q}"}, q=PARENT["q"])
embed = EmbeddingOp.of(resource="bge-m3", texts=PARENT["texts"])
rerank = RerankOp.of(resource="bge-m3", query=PARENT["q"], documents=PARENT["docs"])
```

## PromptOp Wildcard

When `chain()` is used inside `@graph` with a PARENT ref template, wildcard forwarding (`{"*": PARENT}`) auto-discovers template variables from source op's input keys.

## Plugin Registration

Plugins auto-register from YAML resources:
```yaml
llm:
  gpt-4o:
    type: openai
    model: gpt-4o
    api_key: ${OPENAI_API_KEY}
```

## Feature Flags

`[openai]`, `[gemini]`, `[bedrock]`, `[onnx]`, `[huggingface]`, `[embeddings]`, `[rerankers]`, `[all-light]`, `[all]`

## Errors

`PromptError`, `EmbeddingError`, `RerankError` — all from `hush.core.exceptions`
