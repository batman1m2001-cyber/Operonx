# Code Refactoring — Status

Last updated: 2026-03-09
Branch: `feat/stream-architecture`

## What's Done

### 1. Factory Classes → Plain Functions ✅
Replaced 4 unnecessary factory classes with flat `create_*` functions:

| Before | After | File |
|--------|-------|------|
| `LLMFactory.create(config)` | `create_llm(config)` | `hush-providers/hush/providers/llms/factory.py` |
| `EmbeddingFactory.create(config)` | `create_embedding(config)` | `hush-providers/hush/providers/embeddings/factory.py` |
| `RerankingFactory.create(config)` | `create_reranking(config)` | `hush-providers/hush/providers/rerankers/factory.py` |
| `AuthFactory.create(config)` | `create_auth(config)` | `hush-providers/hush/providers/auth/factory.py` |

### 2. Plugin Classes → Module-level Functions ✅
Replaced 5 plugin classes (single `@classmethod register()` + `_registered` flag) with module-level functions:

| Before | After | File |
|--------|-------|------|
| `LLMPlugin.register()` | `llm_plugin.register()` | `hush-providers/.../registry/llm_plugin.py` |
| `EmbeddingPlugin.register()` | `embedding_plugin.register()` | `hush-providers/.../registry/embedding_plugin.py` |
| `RerankPlugin.register()` | `rerank_plugin.register()` | `hush-providers/.../registry/rerank_plugin.py` |
| `AuthPlugin.register()` | `auth_plugin.register()` | `hush-providers/.../registry/auth_plugin.py` |
| `ObservabilityPlugin.register()` | `plugin.register()` | `hush-telemetry/hush/telemetry/plugin.py` |

### 3. @graph Decorator Fix — Static Value Pass-through ✅
Fixed bug in `_decorators.py` line 48 where ALL params were unconditionally wrapped as `PARENT[key]` refs.

**Fix**: `_build_fn_args()` now checks `isinstance(value, Ref)`:
- **Ref values** → wrapped as `PARENT[key]` (resolved at runtime)
- **Static values** → passed through directly (available at graph build time)

`@graph.loop` keeps the old all-PARENT behavior (loop state variables need PARENT refs).

**File**: `hush-core/hush/core/ops/graph/_decorators.py`

### 4. `contain_generation` Added to `_BASE_INIT_KEYS` ✅
Allows `contain_generation=True` to flow through as an init kwarg via `split_shorthand_kwargs`.

**File**: `hush-core/hush/core/ops/_shortcuts.py`

### 5. ChainOp Class → chain() Function ✅
Replaced 213-line `ChainOp(GraphOp)` class with 107-line `chain()` factory function.

**Before**: `ChainOp.of(resource="gpt-4o", template=..., query=PARENT["q"])`
**After**: `chain(resource="gpt-4o", template=..., query=PARENT["q"])`

- Same capabilities: text mode, structured mode (extract + parser), load balancing, fallback, response_format
- Auto-naming works: `my_chat = chain(...) → name == "my_chat"`
- `contain_generation=True` by default
- Config goes to internal LLMOp — no dead `__slots__`

**File**: `hush-providers/hush/providers/ops/chain.py`

### Test Results
- **hush-core**: 618 passed, 1 skipped
- **hush-providers**: 153 passed, 5 skipped (ONNX skip on some envs)
- **hush-telemetry**: 53 passed (from prior session)

## Files Changed

### hush-core
- `hush/core/ops/_shortcuts.py` — added `contain_generation` to `_BASE_INIT_KEYS`
- `hush/core/ops/graph/_decorators.py` — `_build_fn_args()`, static value pass-through
- `tests/ops/test_graph.py` — 7 new tests for static/Ref/mixed params

### hush-providers
- `hush/providers/llms/factory.py` — `LLMFactory` → `create_llm()`
- `hush/providers/embeddings/factory.py` — `EmbeddingFactory` → `create_embedding()`
- `hush/providers/rerankers/factory.py` — `RerankingFactory` → `create_reranking()`
- `hush/providers/auth/factory.py` — `AuthFactory` → `create_auth()`
- `hush/providers/registry/*.py` — Plugin classes → module-level functions
- `hush/providers/ops/chain.py` — `ChainOp` class → `chain()` function
- `hush/providers/ops/__init__.py` — export `chain` instead of `ChainOp`
- `hush/providers/__init__.py` — same
- `tests/test_chain_op.py` — updated for `chain()` API
- `tests/test_shorthand.py` — updated for `chain()` API
- `tests/test_integration.py` — updated imports

### hush-telemetry
- `hush/telemetry/plugin.py` — `ObservabilityPlugin` → module-level functions
- `hush/telemetry/__init__.py` — updated imports
