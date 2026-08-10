# operonx.providers

LLM, embedding, reranker, and ONNX provider ops. Provider backends are
loaded lazily — a tier-1 install (`pip install operonx`) can
`import operonx.providers` without pulling `openai` / `httpx` / `numpy`
/ `torch`. Missing-dep errors surface only when the corresponding
backend is actually accessed.

See [Pick an extra](../guide/00-installation.md#pick-an-extra) on the
installation page for which extra each provider needs.

## Provider ops

The four core provider op types. Each exposes an `Op.of(...)`
classmethod for concise construction with explicit keyword args —
that's the recommended style.

::: operonx.providers.ops.LLMOp
::: operonx.providers.ops.EmbeddingOp
::: operonx.providers.ops.RerankOp

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
- Auth — `KeycloakTokenConfig` (in `operonx.providers.auth`).

## Factory functions

Resolve a config to a backend instance. Used internally by
[`ResourceHub`](registry.md#operonx.core.registry.ResourceHub) — most
users don't call these directly.

::: operonx.providers.llms.create_llm
::: operonx.providers.embeddings.create_embedding
::: operonx.providers.rerankers.create_reranking
::: operonx.providers.auth.create_auth
