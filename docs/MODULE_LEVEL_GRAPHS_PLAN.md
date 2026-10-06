# Module-level graphs everywhere, `operonx.agents` / `operonx.kb`

Status: approved 2026-10-06 ("just go"). Rule: guide 05 — every `@graph` at module level, never
inside a function; settings are graph inputs; edges stay written out.

## Why factories exist today (audit 2026-10-06)

| Where | Factories | Why |
|---|---|---|
| operonx-kb `graphs/*` | 23 | Each collection names its own embedder, vector store, LLM, reranker. Provider ops (`LLMOp`, `EmbeddingOp`, `VectorSearchOp`, `VectorUpsertOp`, `VectorDeleteOp`, `RerankOp`, `DocFetchOp`) take `resource=` only when the graph is built, so a graph was generated per collection. The rest (`k`, depths, door wrappers) was habit. |
| operonx `Service(variants=)` + `examples/ex18_variants` | 1 feature | A variant calls a factory with its bound arguments and compiles the returned graph. Studio's extractor does the same. |
| operonx `operonx/agents/graphs/*` | 4 | The deprecated agents module (replaced by operonx-agents). |
| operonx-agents `scripts/bench_*`, standalone callbot `scripts/bench_*` | 14 | Throwaway measurement scripts. |
| callbot `staging` | 2 | Out of scope (callbot work stays on `refactor/operonx-studio`, which is clean). |

`meeting-prep-operonx` and `meeting-prep-brd` (seminar materials) still import the deprecated
`operonx.agents` (`build_react_agent`, `.mcp`, `.memory`, `.policy`, `tool`). qc-snatcher is clean.

## Decisions

| # | Decision |
|---|---|
| M1 | **`resource=` may be a graph input.** Every provider op accepts a string (as now: fixed when built) or a `Ref` / graph input (resolved per call, the backend cached per key). One module-level graph serves any embedder or model the run names. |
| M2 | **Variants bind graph inputs, not factory arguments.** `[serve.variants]` / `Service(variants={"formal": {"style": "formal"}})` compiles the *same* module-level graph once per variant with those inputs fixed; a request cannot override a bound input. Factories are refused with a message pointing at guide 05. Studio's extractor reads variants the same way. |
| M3 | **The old `operonx.agents` module is deleted** (deprecated since 1.14; no deprecation window, per the earlier D4 decision). |
| M4 | **`operonx.agents` and `operonx.kb` become aliases** of the separately installed `operonx_agents` and `operonx_kb` (user decision 2026-10-06): `from operonx.agents import Agent`, `from operonx.kb import KnowledgeBase`, submodules too (`operonx.agents.testing`). A missing package raises `ImportError` naming the pip install. `operonx_agents` / `operonx_kb` keep working. Docs and templates use the short form. |
| M5 | **operonx-kb has no graph factories.** Its graphs are module level; `KnowledgeBase` passes a collection's spec values and resource keys as inputs; the choice of mode picks which module-level graph runs. Retrieval results must be identical (the suite's exact-hit tests plus the K6 graph tests). |
| M6 | meeting-prep-operonx / -brd move to operonx-agents (`Agent`, `Runner`, `MCPToolset`, `tool`) with the short imports; their behaviour checks (tests, evals with a fake model) stay green. |
| M7 | Bench scripts with factories are rewritten only if still used; otherwise deleted. |

## Phases (each: one branch per repo, merge when its gate passes)

1. **operonx 1.16.0** — M1, M2 (ex18 rewritten, guide 02/05 updated), M3, M4. Gate: full suite,
   guide tests, callbot suite on `refactor/operonx-studio`.
2. **operonx-studio** — extractor and variant views on M2. Gate: studio suite.
3. **operonx-kb 0.3.0** — M5. Gate: KB suite (SQLite, Postgres, pgvector); no `@graph` indented in
   `operonx_kb/`.
4. **operonx-agents 0.2.0** — short imports in docs and templates, bench scripts (M7). Gate: suite.
5. **meeting-prep-operonx / -brd** — M6. Gate: their tests.
6. **Audit again** — `git grep -nE '^\s+@graph'` across every repo: only docstrings, tests and the
   guide's "no" example remain.
