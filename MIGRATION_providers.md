# Migration Plan: `hush-providers` → Operon

## Overlap analysis

Comparing `hush-providers/hush/providers/` vs `operonx/core/`:

| Dir | In core? | In providers? | Nature of overlap |
|-----|----------|---------------|-------------------|
| `ops/` | ✓ (base, flow, transform, graph) | ✓ (llm, embedding, rerank, onnx, triton, prompt, chain) | **Same concept, different ops** |
| `registry/` | ✓ (ResourceHub, REGISTRY, storage) | ✓ (llm_plugin, embedding_plugin, rerank_plugin, auth_plugin, onnx_plugin) | **Plugins register to core registry** |
| `_utils.py` | ✓ (core/ops/_utils.py) | ✓ (providers/ops/_utils.py) | **Same filename, different content** |
| `llms/`, `embeddings/`, `rerankers/` | ✗ | ✓ | backends (no overlap) |
| `auth/` | ✗ | ✓ | Keycloak token provider |

## Cross-package imports (hush-providers → hush-core)

Heavy dependency on core:
- `hush.core.configs.OpType`
- `hush.core.exceptions.{EmbeddingError, RerankError}`
- `hush.core.media.Media`
- `hush.core.ops.{BaseOp, END, PARENT, START, ParserOp, graph}`
- `hush.core.ops.base.{shorthand, split_shorthand_kwargs}`
- `hush.core.utils.{YamlModel, common.Param}`
- `hush.core.registry.REGISTRY`

All become `operonx.core.*` after rename — straightforward.

## Three options

### Option A: Sibling under `operonx/` (minimal restructure) ⭐ recommended
```
operonx/
├── core/                       (existing, unchanged)
│   ├── ops/          (base, flow, transform, graph)
│   └── registry/     (ResourceHub, REGISTRY)
└── providers/                  (new, mirrors hush-providers)
    ├── __init__.py
    ├── ops/          (llm, embedding, rerank, onnx, triton, prompt, chain)
    ├── llms/         (OpenAI, Azure, Gemini, Anthropic, vLLM, ...)
    ├── embeddings/
    ├── rerankers/
    ├── auth/         (Keycloak)
    ├── registry/     (plugin registrations)
    └── _utils.py
```
Imports: `from operonx.providers import LLMOp` (mirrors `from operonx.core import Operon`)
- **Pros**: Clean boundary, easy port (1:1 mapping), matches core migration style
- **Cons**: "providers" subnamespace stays — could feel like "sub-projects merely renamed"

### Option B: Fully flat (everything under `operonx/`)
```
operonx/
├── engine.py, exceptions.py, media.py, ...   (from core)
├── ops/                       (merged: base + core ops + provider ops)
│   ├── base.py
│   ├── flow/, transform/, graph/
│   ├── llm.py, embedding.py, rerank.py, onnx.py, triton.py, prompt.py, chain.py
├── llms/, embeddings/, rerankers/             (backends)
├── auth/
├── registry/                   (merged: ResourceHub + all plugins)
├── states/, tracing/, configs/, loggings/, utils/
```
Imports: `from operonx import Operon, LLMOp, GraphOp` — one namespace
- **Pros**: True merge, flattest structure possible
- **Cons**: **Requires undoing the `operonx/core/` migration I just did** — moving files up one level, rewriting imports again. ~62 files to rewrite.

### Option C: Hybrid — merge `ops/` and `registry/`, keep rest in `providers/`
```
operonx/
├── core/                       (engine, states, tracing, exceptions, media, etc.)
├── ops/                        (merged: base + core + provider ops)
├── registry/                   (merged core + provider plugins)
├── providers/                  (ONLY backends now)
│   ├── llms/, embeddings/, rerankers/, auth/
```
- **Pros**: Kills the two real overlaps (`ops/`, `registry/`)
- **Cons**: Inconsistent — `ops/` and `registry/` at top, but engine stuff buried in `core/`. Weird.

## My recommendation: Option A

Ship Option A for now — it's a clean 1:1 port mirroring the core migration. If you decide you want to flatten later, Option B becomes a single "move everything up + rewrite imports" pass once all subsystems are migrated.

Rationale:
- Consistent with the `core/` migration already done
- Cross-imports stay clear (`operonx.core.X` vs `operonx.providers.Y`)
- Re-exports at top-level `operonx/__init__.py` can give users both styles

## Execution steps (assumes Option A)

1. Copy `hush/providers/` → `operonx/providers/` verbatim
2. Copy `hush-providers/tests/` → `tests/providers/`
3. Global rename:
   - `from hush.core` → `from operonx.core`
   - `from hush.providers` → `from operonx.providers`
   - `import hush.providers.registry` → `import operonx.providers.registry`
   - `hush-providers` → `operonx-providers` (docstrings)
4. Update `pyproject.toml` — merge optional-dep groups (openai, gemini, anthropic, bedrock, onnx, huggingface) already present
5. Top-level `operonx/__init__.py` — re-export `LLMOp`, `EmbeddingOp`, etc. for `from operonx import LLMOp` convenience (optional)
6. Run `pytest tests/providers/ -q` — expect ~168 passed (hush-providers baseline)

## Out of scope

- Backend-specific work (providers just get copied, no new logic)
- Telemetry migration (separate pass)
- Serve migration (separate pass)
- `providers/__init__.py` chain → chat/ask rename already done in Hush-ai — will carry over
