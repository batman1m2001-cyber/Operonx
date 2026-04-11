# LLMOp I/O Redesign

**Status:** proposed
**Target:** `python/hush-providers/hush/providers/ops/llm.py`
**Motivation:** the current output shape has dead fields, buried cache metrics, and a fake `context_used` value. Now that backend-level prompt caching is in place, this is a good moment to clean up the op's I/O contract.

## Problems today

1. **`tokens_used` is an opaque Pydantic dump.** Callers who want cached-token counts have to know to read `tokens_used["prompt_tokens_details"]["cached_tokens"]`. Cache writes from Anthropic land in `tokens_used["cache_write_tokens"]` only because the backend stashes them as a Pydantic `model_extra`.
2. **`context_used` is fake.** Current implementation: `len(str(messages)) // 4`. This is not a token count; it is not a cost signal; it overlaps with `usage.prompt_tokens` which *is* real.
3. **`error_code` / `error_message` are dead.** Declared in the output schema, never populated anywhere. The fallback path reraises on exhaustion; the success path doesn't touch them.
4. **Output concerns are all at one level.** Content (what every caller reads), metadata (model_used, finish_reason), usage (tokens/cost), and rarely-touched debug info (logprobs, refusal, thinking_content) are all flat siblings — 12 fields total, most unused per call.
5. **`_stream_final` and `_extract_completion` duplicate the output shape.** Adding a field means editing two places.
6. **No first-class cache signals.** The work we did in the backend surfaces `cached_tokens` and `cache_write_tokens`, but they're buried inside `tokens_used`.

## New output shape

Three tiers, aligned with how often each field is read downstream:

```python
# Tier 1 — always top-level, the 90% case
content: str
role: str                     # "assistant"
finish_reason: str            # "stop" | "tool_calls" | "length"
model_used: str
tool_calls: list

# Tier 2 — named, flat cost/usage metrics (NEW)
usage: dict = {
    "prompt_tokens": int,
    "completion_tokens": int,
    "total_tokens": int,
    "cached_tokens": int,         # cache HIT   — all providers
    "cache_write_tokens": int,    # cache WRITE — Anthropic only, 0 elsewhere
    "reasoning_tokens": int,      # if model reports it (o1, Claude thinking, DeepSeek-R1)
}

# Tier 3 — bag for uncommon fields, only populated when present
extras: dict = {
    "thinking_content": str | None,
    "refusal": str | None,
    "logprobs": dict | None,
}
```

### Rationale

- **Named cost fields.** `llm["usage"]["cached_tokens"]` is discoverable. No Pydantic internals required.
- **`extras` bag.** Adds a growth path for provider-specific metadata (safety ratings, grounding, citations) without expanding the schema.
- **Drops dead fields.** Three unused fields gone, one fake metric gone.
- **Non-breaking for the 95% case.** Any graph reading `content`, `role`, `finish_reason`, `model_used`, `tool_calls` is unaffected.

## Removed fields

| Field | Reason |
|---|---|
| `tokens_used` | Replaced by `usage`, flat and named |
| `context_used` | Fake metric (`len(str(messages))//4`); `usage.prompt_tokens` is the real answer |
| `error_code` | Never populated anywhere |
| `error_message` | Never populated anywhere |
| `thinking_content` (top-level) | Moved into `extras.thinking_content` |
| `refusal` (top-level) | Moved into `extras.refusal` |
| `logprobs` (top-level) | Moved into `extras.logprobs` |

## Input schema

**No changes.** Caching is already automatic at the backend level; no per-op flag needed.

## Impact surface (files that touch removed fields)

Found via `grep -r "tokens_used\|context_used\|error_code\|thinking_content\|refusal\|logprobs"`:

### Must update (cross-package dependency)

1. **[python/hush-icore/hush/core/tracing/collector.py:348](python/hush-icore/hush/core/tracing/collector.py#L348)**
   ```python
   usage = outputs.get("tokens_used") if contain_generation else None
   ```
   → change to `outputs.get("usage")`. The local variable is *already* called `usage`, so this actually makes the collector clearer.

### Must update (within hush-providers)

2. **[python/hush-providers/hush/providers/ops/llm.py](python/hush-providers/hush/providers/ops/llm.py)** — the op itself
   - Update `output_schema` dict
   - Update `_extract_completion()` return dict
   - Update `_stream_final()` return dict
   - Update `_new_stream_acc()` accumulator shape
   - Update `_process_chunk()` accumulator writes
   - **Refactor:** pull shared output construction into `_build_output(source, resource)` used by both paths
   - Update the docstring listing inputs/outputs
3. **[python/hush-providers/hush/providers/ops/chain.py:29](python/hush-providers/hush/providers/ops/chain.py#L29)** — docstring reference
   ```
   Returns raw LLM output: content, role, model_used, tokens_used, etc.
   ```
   → update to mention `usage` instead of `tokens_used`.

### Must update (examples)

4. **[python/hush-providers/examples/batch_llm_node_complex.py:67,77](python/hush-providers/examples/batch_llm_node_complex.py)**
   ```python
   outputs={"content": PARENT["fast_response"], "tokens_used": PARENT["fast_tokens"]}
   ```
   → rename `tokens_used` key to `usage`.

5. **[python/hush-providers/examples/batch_llm_node_simple.py:147](python/hush-providers/examples/batch_llm_node_simple.py#L147)**
   ```python
   tokens = state[f"normal_chat_{i}.chat", "tokens_used", None]
   ```
   → rename to `"usage"`.

### Must update (tests within hush-providers)

6. **[python/hush-providers/tests/test_llm_op.py](python/hush-providers/tests/test_llm_op.py)**
   - L137-140, L840-845: `tokens_used` field reads → `usage`
   - L366-376: `test_output_schema_has_refusal_and_logprobs` — assertion path changes from top-level to `extras.refusal` / `extras.logprobs`
   - L1005-1009: `result["logprobs"]` → `result["extras"]["logprobs"]`

7. **[python/hush-providers/tests/test_llm_streaming.py](python/hush-providers/tests/test_llm_streaming.py)**
   - L126: `final["tokens_used"]["prompt_tokens"]` → `final["usage"]["prompt_tokens"]`
   - L283: `final["thinking_content"]` → `final["extras"]["thinking_content"]`

### Don't need updating

- **[python/hush-icore/examples/demo_logger.py:58](python/hush-icore/examples/demo_logger.py#L58)** — uses the literal key `"tokens_used"` in a standalone demo dict, not reading from an LLMOp. No change needed.
- **[python/hush-icore/tests/ops/graph/test_ref_scope.py](python/hush-icore/tests/ops/graph/test_ref_scope.py)** — uses `refusal` as a generic string in unrelated ref-scope tests, not the LLMOp field.
- **[python/hush-providers/tests/test_llm_streaming.py:22-23](python/hush-providers/tests/test_llm_streaming.py)** — sets `delta.refusal = None` on a mock SDK delta object, unrelated to output schema.

## Implementation steps

1. **Refactor `llm.py`**
   - Rewrite `output_schema` dict with the three-tier shape
   - Add `_build_output(source, resource, raw_inputs)` helper that constructs the output dict from either a `ChatCompletion` or a streaming accumulator
   - Rewrite `_extract_completion()` to delegate to `_build_output()`
   - Rewrite `_stream_final()` to delegate to `_build_output()`
   - Update `_new_stream_acc()` to carry `usage`, `extras.thinking_content`, `extras.refusal` instead of their flat equivalents
   - Update `_process_chunk()` writes accordingly
   - Update the class docstring
2. **Update cross-package consumer:** collector.py line 348 — `tokens_used` → `usage`.
3. **Update in-package consumer:** chain.py docstring.
4. **Update examples:** two `batch_llm_node_*.py` files.
5. **Update tests:** two test files.
6. **Run test suites:**
   ```bash
   cd python/hush-providers && uv run -m pytest tests/test_llm_op.py tests/test_llm_streaming.py -x
   cd python/hush-icore && uv run -m pytest tests/ -x  # sanity-check collector
   ```
7. **Smoke-test end-to-end** via the existing [scripts/test_backend_caching_integration.py](python/hush-providers/scripts/test_backend_caching_integration.py), adapted to also instantiate an `LLMOp` and read the new fields.

## Non-goals

- **No input changes.** Inputs stay exactly as they are. `cache` is not added; caching is on by default at the backend level and users don't need to think about it.
- **No `context_used` replacement.** If anyone actually needs a pre-flight token estimate, `usage.prompt_tokens` from the previous call is the honest signal.
- **No Rust-backend changes.** Rust `hush-icore` serializes ops by their `serialize()` output, which doesn't touch the output schema — this redesign is Python-only.

## Risks

- **Silent downstream readers.** A graph in the wild might read `llm["tokens_used"]` via a PARENT ref. There's no way to detect this statically. Mitigation: the grep above covered all in-repo callers; external users will see a `KeyError` immediately on first run rather than a silent wrong-value, which is the right failure mode.
- **Trace UI drift.** The collector rename is a one-line change, but any downstream tracing backend (Langfuse, OTEL) that reads the collector's output key name would also need updating. Mitigation: the collector's *local variable* is already called `usage`, so the wire shape doesn't change — only the op-side source key.
