# operonx.providers

LLM, embedding, reranker, and ONNX provider ops. Provider backends are
loaded lazily — a tier-1 install (`pip install operonx`) can
`import operonx.providers` without pulling `openai` / `httpx` / `numpy`
/ `torch`. Missing-dep errors surface only when the corresponding
backend is actually accessed.

See [Pick an extra](../guide/00-installation.md#pick-an-extra) on the
installation page for which extra each provider needs.

## Provider ops

The core provider op types. Each exposes an `Op.of(...)` classmethod
for concise construction with explicit keyword args — that's the
recommended style.

::: operonx.providers.ops.LLMOp
::: operonx.providers.ops.EmbeddingOp
::: operonx.providers.ops.RerankOp

### Retrieval (1.1.0)

`VectorSearchOp` and `DocFetchOp` are a **pair**. The vector index is
derived data holding vectors, ids, and filterable metadata; document
content lives in the store of record. `VectorSearchOp` answers *which*
documents, `DocFetchOp` answers *what they say* — and returns rows in
the same order as the ids it was given, so hits stay aligned with their
scores.

See the [RAG guide](../guide/04-rag.md) for the full pipeline and
`operonx/providers/vector_stores/README.md` for each backend's filter
dialect.

::: operonx.providers.ops.VectorSearchOp
::: operonx.providers.ops.DocFetchOp

### Ordering helpers

Vector search returns score-ordered ids; key-based fetches return
arbitrary order. `DocFetchOp` reconciles them internally — these are
exported for anyone writing their own fetch op.

::: operonx.providers.doc_stores.reorder_by_ids
::: operonx.providers.doc_stores.partition_by_ids

## Structured output (1.0.0)

`LLMOp` gained inline parsing + validators + error-guided semantic
retry in 1.0.0 — pass `fields=`, `parser=`, `validators=`, `max_retries=`
directly to `LLMOp.of()` (see class docs above). The old standalone
`ask()` helper was removed.

For pure text parsing without an LLM call, use the pure functions in
`operonx.providers.parsing`:

::: operonx.providers.parsing.parse_and_extract
::: operonx.providers.parsing.ExtractField

## Resource resolution

Backend selection happens by **name**, not by direct construction.
Wire your `resources.yaml`:

```yaml
llm:gpt-4o-mini:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1
  model: gpt-4o-mini

embedding:openai:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1
  model: text-embedding-3-small
  dimensions: 1536
```

Then reference by key in your op definitions:

```python
llm = LLMOp.of(resource="gpt-4o-mini", prompt=PARENT["msgs"])
embed = EmbeddingOp.of(resource="openai", texts=PARENT["docs"])
```

Full reference — including the five disambiguated failure branches
when a key is missing or unset — is in
[Resource hub](../architecture/resource-hub.md).

## Config classes

The Pydantic models behind `resources.yaml`. You rarely construct
these directly; the framework loads them from YAML. Listed here for
reference.

- LLM — `LLMConfig`, `OpenAIConfig`, `AzureConfig`, `GeminiConfig`,
  `AnthropicConfig`, `LLMType` (in `operonx.providers.llms`).
- Embedding — `EmbeddingConfig`, `EmbeddingType`
  (in `operonx.providers.embeddings`).
- Reranker — `RerankingConfig`, `RerankingType`
  (in `operonx.providers.rerankers`).
- Vector store — `VectorStoreConfig`, `VectorStoreType`,
  `VectorStoreMetric` (in `operonx.providers.vector_stores`).
- Document store — `DocStoreConfig`, `DocStoreType`
  (in `operonx.providers.doc_stores`).
- Auth — `KeycloakTokenConfig` (in `operonx.providers.auth`).

## Factory functions

Resolve a config to a backend instance. Used internally by
[`ResourceHub`](registry.md#operonx.core.registry.ResourceHub) — most
users don't call these directly.

::: operonx.providers.llms.create_llm
::: operonx.providers.embeddings.create_embedding
::: operonx.providers.rerankers.create_reranking
::: operonx.providers.vector_stores.create_vector_store
::: operonx.providers.doc_stores.create_doc_store
::: operonx.providers.auth.create_auth
