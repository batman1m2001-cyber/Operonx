# Refactor Plan: Code Quality Audit

Discovered during audit on 2026-04-05. Items are grouped by theme, ordered by
priority within each group. Each item has a clear scope so it can be done as
an independent PR.

---

## Priority Legend

| Symbol | Meaning |
|--------|---------|
| 🔴 | Critical — correctness/security bug or data loss risk |
| 🟠 | High — breaks observability, leaks memory, blocks the event loop |
| 🟡 | Medium — duplicate code, architectural smell, test quality |
| 🟢 | Low — polish, minor inconsistency, dead code |

---

## GROUP 1 — Bugs (fix immediately)

### 1.1 🔴 Gemini credentials via `__dict__`
**File:** `python/hush-providers/hush/providers/llms/gemini.py:66`

`service_account.Credentials.from_service_account_info(self.config.__dict__, ...)`
passes all Pydantic instance internals (including `__fields_set__`, `__pydantic_*`)
to Google's SDK. Risk: private key leaks into error messages or debug output.

**Fix:** Replace with `self.config.model_dump()` and pass only required keys.

---

### 1.2 🔴 `__repr__` crashes with `AttributeError`
**Files:**
- `python/hush-telemetry/hush/telemetry/tracers/langfuse.py:266-269`
- `python/hush-telemetry/hush/telemetry/tracers/otel.py:261-264`

When constructed with `resource=` (no `config`), `self._config` is `None`.
`__repr__` then does `self._config.host` → `AttributeError`. `repr()` must
never raise.

**Fix:**
```python
def __repr__(self) -> str:
    if self._resource:
        return f"<LangfuseTracer resource={self._resource!r}>"
    host = self._config.host if self._config else "?"
    return f"<LangfuseTracer host={host!r}>"
```

---

### 1.3 🔴 Response model class-level defaults evaluated once
**Files:**
- `python/hush-providers/hush/providers/embeddings/vllm.py`
- `python/hush-providers/hush/providers/rerankers/vllm.py`
- `python/hush-providers/hush/providers/rerankers/tei.py`
- `python/hush-providers/hush/providers/rerankers/pinecone.py`

```python
class EmbeddingResponse(BaseModel):
    id: str = str(uuid.uuid4())   # evaluated ONCE at class definition
    created: int = time.time()    # same — all instances share identical value
```

**Fix:** Use `default_factory`:
```python
id: str = Field(default_factory=lambda: str(uuid.uuid4()))
created: int = Field(default_factory=lambda: int(time.time()))
```

---

### 1.4 🟠 Gemini token refresh blocks event loop
**File:** `python/hush-providers/hush/providers/llms/gemini.py:102-118`

`_refresh_token()` is a sync method with `time.sleep()` called from within an
async class. Blocks the entire event loop during token refresh — all concurrent
requests stall.

**Fix:** Make `_refresh_token()` async, replace `time.sleep()` with
`await asyncio.sleep()`, call with `await` at the call site.

---

### 1.5 🟠 FlushWorker futures list grows unbounded
**File:** `python/hush-icore/hush/core/tracing/flush_worker.py:51-52`

`self._futures: List[Future]` is appended to on every workflow execution but
never pruned. Long-running servers (hush-serve) will slowly leak memory.

**Fix:** After `executor.submit()`, store the future and prune completed ones:
```python
self._futures = [f for f in self._futures if not f.done()]
self._futures.append(executor.submit(...))
```

---

### 1.6 🟠 Op-level cache leaks between tests
**File:** `python/hush-icore/hush/core/ops/base.py:120-121`,
`python/hush-icore/tests/conftest.py`

`BaseOp._cache_stores: Dict[str, tuple] = {}` is a class-level dict. The
class-level design is intentional and correct for production: the cache is
content-addressed by `(full_name, input_hash)`, so the same op + same inputs
always returns the correct cached result across runs. However, between pytest
test cases within a session, accumulated entries can cause cache hits in tests
that expect fresh execution.

**Fix:** Add `autouse` fixture to clear `_cache_stores` between tests. No
structural change to the cache itself.

```python
# tests/conftest.py
@pytest.fixture(autouse=True)
def clear_op_cache():
    from hush.core.ops.base import BaseOp
    yield
    BaseOp._cache_stores.clear()
```

---

## GROUP 2 — Architecture

### 2.1 🟠 Add `BaseOp.warmup()` + `Hush._warmup_ops()` (cold start fix)
**Files:**
- `python/hush-icore/hush/core/ops/base.py`
- `python/hush-icore/hush/core/engine.py`
- `python/hush-providers/hush/providers/ops/llm.py`
- `python/hush-providers/hush/providers/ops/embedding.py`
- `python/hush-providers/hush/providers/ops/rerank.py`
- `python/hush-providers/hush/providers/ops/onnx.py`

Currently all provider ops lazy-init their backends on the **first user request**
(cold start latency). ONNX/HuggingFace load model weights from disk — seconds.

**Why lazy init exists:** graph construction happens before `ResourceHub` is
configured (graphs are defined at module level, hub is set up later). Ops store
`resource=` as a string and defer `hub.get()` to first execution.

**Warmup placement:** `Hush.__init__()` calls `graph.build()` — at that point
the hub is already configured. Warmup belongs here, right after `build()`.

**Recursive propagation:** `graph.build()` already recurses into all nested
`GraphOp`s depth-first (`graph_op.py:269`). Warmup uses the same walk pattern
via a `_iter_all_ops()` generator — one call on the root covers every leaf op
at any nesting depth. No need to call warmup on child graphs separately.

**Hub is a singleton — no extra instances created:** `_ensure_initialized()`
calls `ResourceHub.instance()` or `get_hub()`, both return the same singleton
object set at server start. The hub's `_cache: Dict[str, CacheEntry]` is
shared — each resource is instantiated once and reused forever.

**Time cost:** warmup is a one-time cost per resource per process:
- OpenAI/Azure/vLLM: ~5-20 ms (creates `httpx.AsyncClient`, no actual connection yet)
- ONNX/HuggingFace: 1-30 s (loads model weights from disk)
After warmup, every `hub.get()` is an O(1) dict lookup.

**Also absorbs item 3.1** — no `ProviderOp` base class needed. The only real
duplication across the 4 ops is the hub-lookup try/except (4 lines). The rest
of each `_ensure_initialized()` is completely different per op (LLMOp handles
load balancing + batch coordinator; EmbeddingOp/RerankOp load one backend;
OnnxOp loads session + tokenizer). A shared base class would be an abstraction
for 4 lines — not worth the inheritance complexity. Extract just the lookup:

```python
# hush-providers/hush/providers/ops/_utils.py  (new, 6 lines)
def resolve_hub():
    """Return the active ResourceHub singleton."""
    try:
        return ResourceHub.instance()
    except RuntimeError:
        return get_hub()
```

Each op's `_ensure_initialized()` replaces its try/except with `hub = resolve_hub()`.

**Full design:**

```python
# hush-icore/ops/base.py — no-op default, correct dependency direction
def warmup(self) -> None:
    """Called by Hush engine after build. Override to eagerly load backends."""
    pass

# hush-providers/ops/llm.py (and embedding.py, rerank.py, onnx.py)
def warmup(self) -> None:
    self._ensure_initialized()

def _ensure_initialized(self):
    if self._initialized:
        return
    hub = resolve_hub()          # ← replaces try/except block
    # ... op-specific loading stays exactly as-is ...
    self._initialized = True

# hush-icore/engine.py — Hush.__init__, after graph.build()
def _warmup_ops(self) -> None:
    from hush.core.registry.shortcuts.global_hub import _get_global_hub
    if _get_global_hub() is None:
        return   # hub not configured yet — stay lazy, no crash
    for op in self._iter_all_ops(self.graph):
        op.warmup()

@staticmethod
def _iter_all_ops(graph) -> Iterator[BaseOp]:
    for op in graph._ops.values():
        yield op
        if isinstance(op, GraphOp):
            yield from Hush._iter_all_ops(op)
```

**Resulting flow:**
```
Graph definition   → resource= string stored, hub not touched
set_global_hub()   → hub singleton set
Hush(graph)
  graph.build()    → schema compiled, topology built
  _warmup_ops()    → walks ALL ops recursively
                      LLMOp.warmup()       → hub.get("llm:gpt-4o")   ~10ms
                      EmbeddingOp.warmup() → hub.get("embedding:bge") ~5s ONNX
                      (hub caches all instances)
engine.run(...)    → _ensure_initialized() is instant (already done)
                      first request has zero cold start
```

This fixes cold start with zero breaking changes and no user burden.

**Tests to add** (`python/hush-icore/tests/test_engine.py` or new `test_warmup.py`):
- `test_warmup_calls_all_ops` — mock `op.warmup()`, verify called on every op
  including ops inside nested `GraphOp`s
- `test_warmup_skips_when_no_hub` — build engine without calling `set_global_hub()`,
  assert no `RuntimeError` raised and ops remain `_initialized = False`
- `test_warmup_idempotent` — call `Hush(graph)` twice on the same graph,
  verify `_ensure_initialized()` only runs once per op (checks `_initialized` flag)

---

### 2.2 🟡 Register error handlers to FastAPI app
**Files:**
- `python/hush-serve/hush/serve/errors.py`
- `python/hush-serve/hush/serve/app.py`

Error handlers (`workflow_exception_handler`, etc.) are defined in `errors.py`
but never added to the FastAPI app — dead code. Users get FastAPI's raw 500
instead of the structured JSON format.

**Fix:** In `HushApp.__init__()` or `HushApp._build_app()`, add:
```python
from hush.serve.errors import workflow_exception_handler
app.add_exception_handler(Exception, workflow_exception_handler)
```

---

### 2.3 🟡 WebSocket handler missing request context
**File:** `python/hush-serve/hush/serve/routes/ws_handler.py`

Two problems:
1. Always generates a new `request_id` per message — ignores middleware, breaks
   distributed tracing.
2. Never passes `user_id` / `session_id` to `engine.start()` — sync and stream
   handlers both pass them; WS is an outlier.

**Fix:**
- Extract `request_id` from the initial WebSocket handshake headers (same as
  HTTP middleware does).
- Pass `user_id`, `session_id`, `request_id` to `engine.start()`.

---

### 2.4 🟡 Stop accessing private tracer attributes in `_rust_bridge.py`
**File:** `python/hush-serve/hush/serve/_rust_bridge.py:105-119`

Directly reads `tracer._config`, `tracer._stream_trace_limit` — private attrs
of hush-telemetry classes. Breaks silently if tracer internals are renamed.

**Fix:** Add a public `to_config_dict() -> dict` method to each tracer's base
class or to `LangfuseTracer`/`OTELTracer` directly, returning only what the
Rust bridge needs. `_rust_bridge.py` calls that method instead.

---

### 2.5 🟡 Circular import between `_edges.py` and `base.py`
**File:** `python/hush-icore/hush/core/ops/_edges.py:9`,
`python/hush-icore/hush/core/ops/base.py:763`

`_edges.py` imports `_set_wildcard_outputs` from `base.py`; `base.py` imports
`DummyOp` from `_edges.py`. Works by load-order accident. Fragile to refactor.

**Fix:** Move `_set_wildcard_outputs` (and any other shared helpers) to a new
`_utils.py` file that neither `base.py` nor `_edges.py` imports from the other
to get. Both import from `_utils.py` instead.

---

## GROUP 3 — Duplicate Code

### ~~3.1~~ Merged into 2.1
Extract hub-lookup helper + `warmup()` — see item 2.1.

---

### 3.2 🟡 Extract HuggingFace model loading into shared util
**Files:**
- `python/hush-providers/hush/providers/embeddings/huggingface.py:45-76`
- `python/hush-providers/hush/providers/rerankers/huggingface.py:46-79`

~30 lines of identical code (local path check, `AutoTokenizer`, CUDA
detection, `model.eval()`). Varies only in model class
(`AutoModel` vs `AutoModelForSequenceClassification`).

**Fix:** Create `python/hush-providers/hush/providers/_utils/huggingface.py`:
```python
def load_hf_model(model_path: str, model_cls, tokenizer_cls=AutoTokenizer):
    is_local = os.path.isdir(model_path)
    tokenizer = tokenizer_cls.from_pretrained(model_path, local_files_only=is_local)
    model = model_cls.from_pretrained(model_path, local_files_only=is_local)
    if torch.cuda.is_available():
        model = model.cuda()
    return model.eval(), tokenizer
```

---

### 3.3 🟡 Extract ONNX model loading into shared util
**Files:**
- `python/hush-providers/hush/providers/embeddings/onnx.py:56-80`
- `python/hush-providers/hush/providers/rerankers/onnx.py:48-92`

~30 lines identical (path validation, CUDA provider selection, session creation).

**Fix:** Create `python/hush-providers/hush/providers/_utils/onnx.py`:
```python
def load_onnx_session(model_path: str) -> tuple[ort.InferenceSession, str]:
    """Returns (session, device_str). Raises FileNotFoundError if model missing."""
    ...
```

---

### 3.4 🟡 Extract duplicate output-filtering in route handlers
**Files:**
- `python/hush-serve/hush/serve/routes/sync_handler.py:25`
- `python/hush-serve/hush/serve/routes/stream_handler.py:37`
- `python/hush-serve/hush/serve/routes/ws_handler.py:54`

Same one-liner in 3 handlers:
```python
output = {k: v for k, v in output.items() if not k.startswith("$")}
```

**Fix:** Move to `hush/serve/schema.py` or a `_utils.py`:
```python
def strip_internal_keys(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("$")}
```

---

### 3.5 🟡 Extract duplicate tracer init boilerplate
**Files:**
- `python/hush-telemetry/hush/telemetry/tracers/langfuse.py:39-55`
- `python/hush-telemetry/hush/telemetry/tracers/otel.py:37-53`

Identical `__init__` validation, `resource` property, and `_get_client()` in
both tracers.

**Fix:** Add `ConfigurableTracer(Tracer)` base class in
`hush/telemetry/tracers/_base.py`:
```python
class ConfigurableTracer(Tracer):
    def __init__(self, config=None, resource=None, tags=None):
        super().__init__(tags=tags)
        if config is None and resource is None:
            raise ValueError("Must provide either 'config' or 'resource'")
        if config is not None and resource is not None:
            raise ValueError("Cannot provide both 'config' and 'resource'")
        self._config = config
        self._resource = resource

    @property
    def resource(self):
        return self._resource

    def _get_client(self):
        if self._config is not None:
            return self._make_client(self._config)
        return get_hub().get(self._resource)

    def _make_client(self, config):
        raise NotImplementedError
```

`LangfuseTracer` and `OTELTracer` inherit from `ConfigurableTracer` and
override `_make_client()`.

---

### 3.6 🟡 Extract duplicate parent-checking in `langfuse.py`
**File:** `python/hush-telemetry/hush/telemetry/tracers/langfuse.py:185,231`

Same 3-line condition for generation and span nodes.

**Fix:** Extract to a private method:
```python
def _set_parent(self, body, parent_key, obs_ids, trace_id):
    if parent_key and parent_key in obs_ids and obs_ids[parent_key] != trace_id:
        body["parentObservationId"] = obs_ids[parent_key]
```

---

### 3.7 🟡 Extract duplicate op `__init__` boilerplate
**Files:**
- `python/hush-icore/hush/core/ops/transform/func_op.py:335-369`
- `python/hush-icore/hush/core/ops/transform/parser_op.py:154-162`
- `python/hush-icore/hush/core/ops/flow/branch_op.py:58-65`

All follow: parse schema → `super().__init__()` → normalize user inputs/outputs →
merge. The merge+normalize pattern is identical.

**Fix:** Add `_init_io(input_schema, output_schema, inputs, outputs)` helper
to `BaseOp`, call it from each subclass `__init__`.

---

## GROUP 4 — Inconsistency

### 4.1 🟡 Replace `print()` with `LOGGER` in providers
**Files:**
- `python/hush-providers/hush/providers/embeddings/huggingface.py:53-70`
- `python/hush-providers/hush/providers/rerankers/huggingface.py:54-73`
- `python/hush-providers/hush/providers/embeddings/onnx.py:64,71-72`
- `python/hush-providers/hush/providers/rerankers/onnx.py:56,63,66`
- `python/hush-providers/hush/providers/llms/gemini.py:117`

Production logs miss model loading status. Output goes to stdout.

**Fix:** Replace all `print(...)` with `LOGGER.info(...)` using the existing
`from hush.core import LOGGER` import.

---

### 4.2 🟡 Replace magic logging level numbers with constants
**File:** `python/hush-icore/hush/core/ops/base.py:510,629,657`

`LOGGER.isEnabledFor(20)` / `(30)` / `(40)` are unreadable.

**Fix:**
```python
import logging
LOGGER.isEnabledFor(logging.INFO)    # was 20
LOGGER.isEnabledFor(logging.WARNING) # was 30
LOGGER.isEnabledFor(logging.ERROR)   # was 40
```

---

### 4.3 🟡 Standardize ruff config across all packages
**Files:** All `python/*/pyproject.toml`

Each package has different ignored ruff rules with no explanation. No monorepo
root ruff config to inherit from.

**Fix:**
1. Create `ruff.toml` at monorepo root with baseline rules + ignore list.
2. Each package's `pyproject.toml` sets `extend = "../../ruff.toml"` to inherit.
3. Package-specific overrides only where genuinely needed (document why).

---

### 4.4 🟢 Fix split `datetime`/`timedelta` import in `langfuse.py`
**File:** `python/hush-telemetry/hush/telemetry/tracers/langfuse.py:1,107`

`datetime` and `timezone` imported at top level; `timedelta` imported inside
`flush()` on every call.

**Fix:** Move `from datetime import timedelta` to top-level imports.

---

### 4.5 🟢 Standardize `asyncio_mode` and pytest config
**Files:** All `python/*/pyproject.toml`

`asyncio_mode = "auto"` duplicated in every package. `asyncio_default_fixture_loop_scope`
only in hush-telemetry. Duplicate `[project.optional-dependencies]` dev deps
conflict with `[dependency-groups]` dev deps.

**Fix:**
1. Use `[dependency-groups]` as single source of truth, remove old optional-dependencies dev entries.
2. Consider a shared `pytest.ini` or `pyproject.toml` at monorepo root that
   packages extend.

---

## GROUP 5 — Dead Code

### 5.1 🟡 Remove or implement unused OTELTracer static methods
**File:** `python/hush-telemetry/hush/telemetry/tracers/otel.py`

`_get_short_name()` and `_context_to_str()` are defined but appear unused.

**Fix:** Delete both, or move to a shared utils module if they're planned for
future use (and add a TODO comment).

---

### 5.2 🟢 Remove `main()` debug function from `llms/factory.py`
**File:** `python/hush-providers/hush/providers/llms/factory.py`

Calls `LLMConfig.default()` which doesn't exist — would crash if run.

**Fix:** Delete the `main()` function and `if __name__ == "__main__"` block.

---

### 5.3 🟢 Remove empty `__slots__ = []` from `PromptOp`
**File:** `python/hush-providers/hush/providers/ops/prompt.py:51`

Empty slots declaration does nothing and adds noise.

**Fix:** Delete the line.

---

### 5.4 🟢 Remove 80-line `SETUP_TUTORIAL` string from test conftest
**File:** `python/hush-telemetry/tests/conftest.py`

Never displayed or used.

**Fix:** Delete the block.

---

### 5.5 🟢 Remove unused `failing_graph`/`failing_op` test fixtures
**File:** `python/hush-serve/tests/conftest.py`

Defined but never referenced in any test.

**Fix:** Delete both fixtures, or write the missing error-path tests that use
them.

---

### 5.6 🟢 Fix `ParserOp.format` type signature
**File:** `python/hush-icore/hush/core/ops/transform/parser_op.py:134`

Signature says `format: Optional[ParserType] = None` but the body immediately
does `self.format = format or "xml"` — `None` is never actually stored.
The type hint lies to both the user and the type checker.

**Fix:** One-line change:
```python
# before
format: Optional[ParserType] = None

# after
format: ParserType = "xml"
```

---

## GROUP 6 — Test Quality

### 6.1 🟡 Strengthen `test_imports.py` assertions
**File:** `python/hush-providers/tests/test_imports.py`

Tests only check `assert BaseLLM is not None` — catches nothing real.

**Fix:** Assert on interface, not existence:
```python
def test_llm_imports():
    from hush.providers import BaseLLM, LLMConfig, LLMType
    assert hasattr(BaseLLM, "generate")
    assert hasattr(BaseLLM, "stream")
    assert LLMType.OPENAI.value == "openai"
    assert issubclass(LLMConfig, YamlModel)
```

---

### 6.2 🟡 Add error-path tests for hush-serve handlers
**File:** `python/hush-serve/tests/`

No tests for: invalid resource names, missing configs, malformed inputs,
workflow exceptions. The `failing_graph` fixture was created for this but
the tests were never written.

**Fix:** Write at least one test per handler (sync/stream/WS) that exercises
the error path and verifies the structured JSON error response format.

---

### 6.3 🟢 Add comments for magic test values
**Files:**
- `python/hush-icore/tests/test_concurrent.py:14` — `CCU = 5`
- `python/hush-telemetry/tests/conftest.py:122-172` — hardcoded UUIDs, timestamps
- `python/hush-providers/tests/test_anthropic.py:238,246` — token limits

**Fix:** Add a one-line comment explaining why each value was chosen.

---

## Execution Order

Dependencies exist between some items. Suggested PR sequence:

```
Batch 1 (independent bug fixes — ship fast):
  1.1  Gemini __dict__ → model_dump()
  1.2  __repr__ null check
  1.3  Response model default_factory
  1.4  Gemini async sleep
  1.5  FlushWorker future pruning
  1.6  Cache class var → instance var

Batch 2 (foundation for later refactors):
  2.5  Fix circular import _edges/base (unblocks 3.7)
  3.5  ConfigurableTracer base (unblocks 2.4)

Batch 3 (architecture):
  2.1  resolve_hub() helper + BaseOp.warmup() + Hush._warmup_ops()
       (absorbs 3.1 — no ProviderOp base class needed)
  2.2  Register error handlers
  2.3  WebSocket request context
  2.4  Stop accessing private tracer attrs (needs 3.5 first)

Batch 4 (duplicate elimination):
  3.2  HuggingFace util
  3.3  ONNX util
  3.4  strip_internal_keys
  3.6  _set_parent helper
  3.7  _init_io helper (needs 2.5 first)

Batch 5 (polish):
  4.x  All inconsistency items
  5.x  All dead code removals
  6.x  Test quality
```

---

## Files Modified Summary

| Package | Files |
|---------|-------|
| `hush-icore` | `ops/base.py`, `ops/_edges.py`, `ops/_utils.py` (new), `engine.py`, `tracing/flush_worker.py` |
| `hush-providers` | `llms/gemini.py`, `ops/llm.py`, `ops/embedding.py`, `ops/rerank.py`, `ops/onnx.py`, `ops/_base.py` (new), `embeddings/huggingface.py`, `rerankers/huggingface.py`, `embeddings/onnx.py`, `rerankers/onnx.py`, `_utils/huggingface.py` (new), `_utils/onnx.py` (new), `llms/factory.py`, `ops/prompt.py` |
| `hush-serve` | `app.py`, `errors.py`, `routes/ws_handler.py`, `routes/sync_handler.py`, `routes/stream_handler.py`, `_rust_bridge.py`, `schema.py` |
| `hush-telemetry` | `tracers/langfuse.py`, `tracers/otel.py`, `tracers/_base.py` (new), `tests/conftest.py` |
| All | `pyproject.toml` × 4, `ruff.toml` (new at root) |
