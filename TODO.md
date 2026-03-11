# Hush-ai Major Refactor Plan

## Overview

Three interconnected refactors to improve maintainability and scalability:
1. **Project restructure** — group by language (`python/`, `rust/`)
2. **Engine + Serve redesign** — middleware system, `engine.serve()`, run modes
3. **Documentation overhaul** — auto-generated API docs, doctests, collapse layers

Estimated scope: ~20 phases across 4 milestones.

---

## Milestone 1: Project Restructure

### Phase 1.1: Move Python packages into `python/`

**Move:**
```
hush-core/        → python/hush-core/
hush-providers/   → python/hush-providers/
hush-telemetry/   → python/hush-telemetry/
hush-serve/       → python/hush-serve/
```

**Dissolve `tutorial/`:**
```
tutorial/examples/  → examples/          (top-level, runnable, tested in CI)
tutorial/docs/      → docs/guide/        (manual guide, merged into mkdocs site)
tutorial/pyproject.toml → examples/pyproject.toml (update paths)
```

`tutorial/` directory is deleted after migration.

**Update `pyproject.toml` [tool.uv.sources] paths:**

| File | Old | New |
|------|-----|-----|
| `python/hush-providers/pyproject.toml` | `../hush-core` | `../hush-core` (unchanged — same relative) |
| `python/hush-telemetry/pyproject.toml` | `../hush-core`, `../hush-providers` | same |
| `python/hush-serve/pyproject.toml` | `../hush-core`, `../hush-providers`, `../hush-telemetry` | same |
| `examples/pyproject.toml` | `../hush-core`, etc. | `../python/hush-core`, `../python/hush-providers`, `../python/hush-telemetry` |
| `python/hush-core/pyproject.toml` | (none) | (none) |

> Note: Python packages move into `python/` together — their relative paths stay the same.
> `examples/pyproject.toml` needs updated paths since it's at root, not inside `python/`.

**Delete:** `rush-core/pyproject.toml` (Python benchmarking shim — move bench_e2e.py to `rust/rush-core/benches/` and run with `uv run --directory python/hush-core`)

### Phase 1.2: Move Rust crates into `rust/`

**Move:**
```
rush-core/        → rust/rush-core/
rush-providers/   → rust/rush-providers/
rush-telemetry/   → rust/rush-telemetry/
rush-serve/       → rust/rush-serve/
ui-hush-eyes/     → rust/hush-eyes/
Cargo.toml        → rust/Cargo.toml
Cargo.lock        → rust/Cargo.lock
```

**Update `rust/Cargo.toml` workspace:**
```toml
[workspace]
resolver = "2"
members = [
    "rush-core",
    "rush-providers",
    "rush-serve",
    "rush-telemetry",
    "hush-eyes",
]
```

**Update Cargo.toml path dependencies (all relative, same structure):**
- No changes needed — crates stay in the same relative positions to each other.

**Update `rust/rush-core/benches/bench_e2e.py`:**
- Update paths to find Python packages at `../../python/hush-core/` etc.

### Phase 1.3: Update CI/CD workflows

**Files to update:**
- `.github/workflows/format.yaml` — ruff paths: `hush-core/` → `python/hush-core/`
- `.github/workflows/tests.yaml` — working-directory: `python/${{ matrix.package }}`
- `.github/workflows/python-compatibility.yaml` — same path updates
- `.github/workflows/rust-runtime.yaml` — add `working-directory: rust` or update cargo commands with `--manifest-path rust/Cargo.toml`

### Phase 1.4: Update root config files

- `.pre-commit-config.yaml` — update ruff paths
- Root `CLAUDE.md` — update all path references
- All per-package `CLAUDE.md` files — update cross-references
- `architecture/` — update file path references
- `.gitignore` — update `target/` → `rust/target/`

### Phase 1.5: Add MODULE_MAP.md

Create `MODULE_MAP.md` at root — the Rosetta Stone between Python and Rust:

```markdown
# Module Map: Python ↔ Rust

| Domain | Python | Rust | Notes |
|--------|--------|------|-------|
| **Engine** | `python/hush-core/hush/core/engine.py` | `rust/rush-core/src/engine.rs` | |
| **Op base** | `python/hush-core/hush/core/ops/base.py` | `rust/rush-core/src/ops/base.rs` | |
| **GraphOp** | `python/hush-core/hush/core/ops/graph/graph_op.py` | `rust/rush-core/src/ops/graph/graph_op.rs` | |
| **Scheduler** | `python/hush-core/hush/core/ops/graph/scheduler.py` | `rust/rush-core/src/ops/graph/graph_op.rs` | Merged in Rust |
| **State** | `python/hush-core/hush/core/states/state.py` | `rust/rush-core/src/states/state.rs` | |
| **StateSchema** | `python/hush-core/hush/core/states/schema.py` | N/A | Build-time only |
| **Config** | `python/hush-core/hush/core/configs/` | `rust/rush-core/src/config.rs` | |
| **Tracing** | `python/hush-core/hush/core/tracing/` | `rust/rush-core/src/tracing/` | |
| **TraceCollector** | `python/hush-core/hush/core/tracing/collector.py` | `rust/rush-core/src/tracing/collector.rs` | |
| **LLM ops** | `python/hush-providers/hush/providers/llms/` | `rust/rush-providers/src/llms/` | |
| **Embeddings** | `python/hush-providers/hush/providers/embeddings/` | `rust/rush-providers/src/embeddings/` | |
| **Rerankers** | `python/hush-providers/hush/providers/rerankers/` | `rust/rush-providers/src/rerankers/` | |
| **Prompt op** | `python/hush-providers/hush/providers/ops/prompt.py` | `rust/rush-providers/src/ops/prompt.rs` | |
| **Parser op** | `python/hush-providers/hush/providers/ops/parser.py` | `rust/rush-providers/src/ops/parser.rs` | |
| **Langfuse** | `python/hush-telemetry/hush/telemetry/tracers/langfuse.py` | `rust/rush-telemetry/src/langfuse/` | |
| **OTEL** | `python/hush-telemetry/hush/telemetry/tracers/otel.py` | `rust/rush-telemetry/src/otel/` | |
| **HushEyes** | `python/hush-telemetry/hush/telemetry/tracers/hush_eyes.py` | `rust/rush-telemetry/src/hush_eyes.rs` | |
| **HTTP serve** | `python/hush-serve/hush/serve/` | `rust/rush-serve/src/` | |
```

### Phase 1.6: Verify everything builds

```bash
# Python
cd python/hush-core && uv sync --all-extras && uv run pytest
cd python/hush-providers && uv sync --all-extras && uv run pytest
cd python/hush-telemetry && uv sync --all-extras && uv run pytest
cd python/hush-serve && uv sync --all-extras && uv run pytest

# Rust
cd rust && cargo test --workspace

# CI — push to branch, verify all workflows pass
```

---

## Milestone 2: Engine + Serve Redesign

### Phase 2.1: Middleware system for Hush engine

**File:** `python/hush-core/hush/core/middleware.py` (new)

```python
class Middleware:
    """Base class for engine middleware."""

    async def before_run(self, graph, inputs: dict, context: dict) -> dict:
        """Called before graph execution. Can modify inputs."""
        return inputs

    async def after_run(self, graph, inputs: dict, result: dict, context: dict) -> dict:
        """Called after graph execution. Can modify result."""
        return result

    async def on_error(self, graph, inputs: dict, error: Exception, context: dict) -> None:
        """Called when graph execution fails."""
        raise error
```

**Built-in middleware:**

| Middleware | Location | Purpose |
|-----------|----------|---------|
| `TracingMiddleware` | `hush/core/middleware/tracing.py` | Wraps current tracer logic (replaces `tracer=` arg) |
| `RetryMiddleware` | `hush/core/middleware/retry.py` | Retry on transient failures |
| `CacheMiddleware` | `hush/core/middleware/cache.py` | Cache results by input hash |
| `TimingMiddleware` | `hush/core/middleware/timing.py` | Log execution time |
| `ValidationMiddleware` | `hush/core/middleware/validation.py` | Validate inputs against graph schema |

**Engine changes (`engine.py`):**

```python
class Hush:
    def __init__(self, graph):
        self._graph = graph
        self._middleware: list[Middleware] = []
        self._compiled = None  # lazy compilation

    def use(self, middleware: Middleware) -> "Hush":
        """Add middleware. Returns self for chaining."""
        self._middleware.append(middleware)
        return self

    async def run(self, inputs: dict, **kwargs) -> dict:
        # Apply before_run middleware (in order)
        for mw in self._middleware:
            inputs = await mw.before_run(self._graph, inputs, kwargs)

        # Execute graph
        try:
            result = await self._execute(inputs, **kwargs)
        except Exception as e:
            for mw in reversed(self._middleware):
                await mw.on_error(self._graph, inputs, e, kwargs)
            raise

        # Apply after_run middleware (in reverse order)
        for mw in reversed(self._middleware):
            result = await mw.after_run(self._graph, inputs, result, kwargs)

        return result
```

**Backward compatibility:** Keep `tracer=` arg in `run()` — internally wraps as `TracingMiddleware`. Deprecation warning if used alongside `engine.use(TracingMiddleware(...))`.

### Phase 2.2: Migrate hush-serve into engine.serve()

**Goal:** `HushApp` becomes internal. Users call `engine.serve()`.

**Simplify hush-serve/rush-serve first — strip to 3 routes:**

| Keep | Route | Purpose |
|------|-------|---------|
| `sync_handler` | `POST /path` | REST API (always) |
| `ws_handler` | `WS /path/ws` | WebSocket (callbot) |
| `stream_handler` | `POST /path/stream` | SSE streaming (web UIs) |

| Remove | Route | Rationale |
|--------|-------|-----------|
| `batch_handler` | `POST /path/batch` | Client can call REST N times in parallel |
| `job_handler` | `POST /path/submit` + `GET /jobs/{id}` | Use a proper task queue (Celery) if needed |
| `jobs.py/.rs` | In-memory job store | Only existed for job_handler |

**Files to delete:**
- `python/hush-serve/hush/serve/routes/batch_handler.py`
- `python/hush-serve/hush/serve/routes/job_handler.py`
- `python/hush-serve/hush/serve/jobs.py`
- `rust/rush-serve/src/routes/batch_handler.rs`
- `rust/rush-serve/src/routes/job_handler.rs`
- `rust/rush-serve/src/jobs.rs`

**Update:** `app.py`, `router.rs`, `config.py`, `config.rs` — remove batch/jobs config options and route registration.

**New API:**

```python
engine = Hush(graph)

# Simple serve
engine.serve(port=8000)

# With options
engine.serve(
    port=8000,
    host="0.0.0.0",
    backend="rust",      # or "python" (default)
    stream=True,         # enable SSE endpoint
    websocket=True,      # enable WebSocket endpoint
)

# Multi-endpoint (for multiple graphs)
app = Hush.app()
app.add(graph1, path="/chat")
app.add(graph2, path="/rag")
app.serve(port=8000)
```

**Implementation:**
- `engine.serve()` creates `HushApp` internally, registers single endpoint, calls `app.serve()`
- `Hush.app()` returns a multi-graph app builder (thin wrapper around current `HushApp`)
- `hush-serve` package still exists but re-exports from `hush-core`
- `HushApp` class stays as the internal implementation — not removed
- Lazy import: `from hush.serve import HushApp` only when `.serve()` is called

**Changes:**
- `python/hush-core/hush/core/engine.py` — add `serve()` and `app()` methods (lazy import)
- `python/hush-core/pyproject.toml` — add optional `[serve]` extra: `hush-serve`
- `python/hush-serve/` — simplify internally (drop batch/jobs), `Hush.serve()` is the public API

### Phase 2.3: Run modes

**New methods on `Hush`:**

```python
class Hush:
    # Existing
    async def run(self, inputs: dict, **kwargs) -> dict: ...

    # New run modes
    def serve(self, port=8000, **kwargs): ...

    async def batch(self, inputs_list: list[dict], concurrency=10) -> list[dict]:
        """Run graph concurrently on multiple inputs."""
        sem = asyncio.Semaphore(concurrency)
        async def _run(inp):
            async with sem:
                return await self.run(inp)
        return await asyncio.gather(*[_run(inp) for inp in inputs_list])

    def cli(self):
        """Interactive CLI mode — read JSON from stdin, print result to stdout."""
        import sys, json
        inputs = json.load(sys.stdin)
        result = asyncio.run(self.run(inputs))
        json.dump(result, sys.stdout, indent=2)

    def input_schema(self) -> dict:
        """JSON Schema from graph inputs. Useful for OpenAPI, validation."""
        ...

    def output_schema(self) -> dict:
        """JSON Schema from graph outputs."""
        ...
```

### Phase 2.4: Resource lifecycle + env loading

**Current problems:**
- `ResourceHub` is a global singleton via `get_hub()` — no per-engine isolation
- Users must manually call `load_dotenv()` before anything works
- `HUSH_CONFIG` env var must be set separately
- No way to programmatically configure resources without touching globals

**Proposed:** Engine owns the full config lifecycle: `.env` → `resources.yaml` → manual overrides.

```python
# Zero-config (default) — auto-loads .env + HUSH_CONFIG
engine = Hush(graph)
# Internally does:
#   1. find_dotenv() walk-up → load .env if found
#   2. os.environ["HUSH_CONFIG"] → load resources.yaml if set
#   3. ResourceHub ready

# Explicit paths
engine = Hush(graph, env=".env.production", resources="config/resources.yaml")

# Programmatic override (after auto-load)
engine = Hush(graph)
engine.resources.set("llm", "gpt-4o", OpenAIConfig(api_key="sk-..."))

# No env loading (testing, CI)
engine = Hush(graph, env=False)
engine.resources.set("llm", "mock", MockLLMConfig())
```

**Constructor signature:**
```python
class Hush:
    def __init__(
        self,
        graph: GraphOp,
        *,
        env: str | bool = True,         # True=auto-find, str=path, False=skip
        resources: str | None = None,    # None=use HUSH_CONFIG env var, str=path
    ):
```

**Init sequence:**
1. `env=True` → `find_dotenv()` walk-up, load if found
2. `env="path"` → load that specific `.env` file
3. `env=False` → skip (useful for tests)
4. `resources="path"` → load that YAML file
5. `resources=None` → no resources loaded (ops that need resources will error clearly)
6. Create `ResourceHub` instance owned by this engine

**Remove internal env vars — config goes in code, secrets stay in `.env`:**

| Remove | Current use | Replace with |
|--------|------------|--------------|
| `HUSH_CONFIG` | Path to resources.yaml | `Hush(graph, resources="path")` |
| `HUSH_TRACES_DB` | SQLite path for hush-eyes | `HushEyesTracer(db_path="path")` |
| `HUSH_TRACES_DIR` | Directory for LocalTracer | `LocalTracer(path="path")` |

| Keep | Why |
|------|-----|
| `OPENAI_API_KEY`, `LANGFUSE_*`, etc. | Secrets — belong in `.env`, not in code |
| `LOG_LEVEL` | Standard practice (Python logging / Rust env_logger) |

**Rule:** Secrets go in `.env`. Config goes in code. No more `HUSH_*` env vars for internal wiring.

**Clear error messages when config is missing:**

```python
# No .env found → warning (not error — .env is optional)
# log.warning("No .env file found. Copy env.example to .env and fill in your API keys.")

# Op uses resource that doesn't exist → clear error with fix instructions
# ResourceError: Resource 'llm:gpt-4o' not found.
#   Add to resources.yaml:
#     llm:
#       gpt-4o:
#         type: openai
#         model: gpt-4o
#         api_key: ${OPENAI_API_KEY}
#   Or set programmatically:
#     engine.resources.set("llm", "gpt-4o", {"type": "openai", "model": "gpt-4o"})

# Env var missing for a provider → clear error with exact var name
# ProviderError: OPENAI_API_KEY not set.
#   Add to your .env file:
#     OPENAI_API_KEY=sk-...
```

**Ship starter templates:**

| File | Purpose |
|------|---------|
| `env.example` | All supported env vars with comments (already exists, keep as-is) |
| `resources.starter.yaml` | Minimal working config (OpenAI GPT-4o + one embedding) |

`resources.starter.yaml` lives in the repo root. Users copy it:
```bash
cp resources.starter.yaml resources.yaml
```

**Changes:**
- `Hush.__init__` creates a `ResourceHub` (or uses global as fallback)
- `ResourceHub` passed to ops via execution context (context var)
- Global `get_hub()` still works as fallback for backward compatibility
- Add `python-dotenv` as optional dependency: `[project.optional-dependencies] env = ["python-dotenv"]`
- Improve `ResourceHub.get()` error messages to include fix instructions
- Improve provider error messages to name the exact missing env var

### Phase 2.5: Mirror in Rust (rush-core engine)

**Update `rust/rush-core/src/engine.rs`:**
- Add middleware support (trait-based)
- Add `Rush::serve()` that internally spawns rush-serve
- Schema export methods

```rust
pub trait Middleware: Send + Sync {
    fn before_run(&self, inputs: &mut Value) -> Result<(), RushError> { Ok(()) }
    fn after_run(&self, result: &mut Value) -> Result<(), RushError> { Ok(()) }
    fn on_error(&self, error: &RushError) {}
}

impl Rush {
    pub fn use_middleware(&mut self, mw: impl Middleware + 'static) { ... }
    pub fn serve(self, host: &str, port: u16) -> Result<(), RushError> { ... }
    pub fn input_schema(&self) -> Value { ... }
}
```

---

## Milestone 3: Documentation Overhaul

### Phase 3.1: Set up mkdocs + mkdocstrings

**New files:**
```
docs/
├── mkdocs.yml              # Site config
├── requirements.txt        # mkdocs, mkdocstrings, material theme
├── index.md                # Landing page
├── api/                    # Auto-generated (DO NOT EDIT manually)
│   ├── core/
│   │   ├── engine.md       # ::: hush.core.engine
│   │   ├── ops.md          # ::: hush.core.ops
│   │   └── states.md       # ::: hush.core.states
│   ├── providers/
│   │   ├── llm.md
│   │   ├── embedding.md
│   │   └── rerank.md
│   ├── telemetry/
│   │   └── tracers.md
│   └── serve/
│       └── app.md
├── guide/                  # Manual (migrated from tutorial/docs/)
│   ├── getting-started.md
│   ├── core-concepts.md
│   ├── llm-integration.md
│   ├── control-flow.md
│   ├── embeddings-rag.md
│   ├── tracing.md
│   ├── rust-mode.md
│   └── vi/                 # Vietnamese translations (optional)
│       ├── 00-tong-quan.md
│       └── ...
└── architecture/           # Manual (moved from architecture/)
    ├── execution-flow.md
    ├── state-model.md
    ├── streaming.md
    └── rust-python-split.md
```

**`docs/mkdocs.yml`:**
```yaml
site_name: Hush
site_description: High-performance workflow engine
repo_url: https://github.com/batman1m2001-cyber/Hush-ai

theme:
  name: material
  features:
    - content.code.copy
    - navigation.sections
    - navigation.expand
    - search.suggest

plugins:
  - search
  - mkdocstrings:
      handlers:
        python:
          paths: [python/hush-core, python/hush-providers, python/hush-telemetry, python/hush-serve]
          options:
            show_source: true
            show_root_heading: true
            members_order: source

nav:
  - Home: index.md
  - Guide:
    - Getting Started: guide/getting-started.md
    - Core Concepts: guide/core-concepts.md
    - LLM Integration: guide/llm-integration.md
    - Control Flow: guide/control-flow.md
    - Embeddings & RAG: guide/embeddings-rag.md
    - Tracing: guide/tracing.md
    - Rust Mode: guide/rust-mode.md
  - API Reference:
    - Core: api/core/engine.md
    - Ops: api/core/ops.md
    - State: api/core/states.md
    - Providers: api/providers/llm.md
    - Telemetry: api/telemetry/tracers.md
    - Serve: api/serve/app.md
  - Architecture:
    - Execution Flow: architecture/execution-flow.md
    - State Model: architecture/state-model.md
    - Streaming: architecture/streaming.md
    - Rust-Python Split: architecture/rust-python-split.md
```

### Phase 3.2: Write key docstrings (public API only)

**Priority docstrings (the ~15 classes/functions users actually import):**

| Class/Function | File | Docstring scope |
|---------------|------|-----------------|
| `Hush` | `engine.py` | Constructor, `run()`, `serve()`, `batch()`, `cli()` |
| `GraphOp` | `ops/graph/graph_op.py` | Context manager, `>>`, nested usage |
| `@op` | `ops/transform/func_op.py` | Decorator, `rust=`, `executor=` |
| `@graph` | `ops/graph/graph_decorator.py` | Factory pattern, params → PARENT refs |
| `BaseOp` | `ops/base.py` | Lifecycle, inputs/outputs, `>>` |
| `PARENT`, `START`, `END` | `ops/__init__.py` | State references |
| `Ref` | `states/ref.py` | `op["key"]`, transforms |
| `chain()` | `registry/shortcuts/chain.py` | Prompt+LLM shorthand |
| `LLMOp.of()` | providers `ops/llm.py` | LLM op creation |
| `EmbeddingOp.of()` | providers `ops/embedding.py` | Embedding op creation |
| `RerankOp.of()` | providers `ops/rerank.py` | Rerank op creation |
| `PromptOp.of()` | providers `ops/prompt.py` | Template formatting |
| `Tracer` | `tracing/base.py` | Base class, `flush()` |
| `ForOp.of()` | `ops/flow/for_op.py` | Iteration |
| `WhileOp.of()` | `ops/flow/while_op.py` | Loop until condition |
| `Middleware` | `middleware.py` | Hook system |

**Docstring format (Google style, mkdocstrings-compatible):**
```python
class Hush:
    """Workflow engine — compiles and runs GraphOp workflows.

    Args:
        graph: The GraphOp to execute.

    Example:
        >>> from hush.core import Hush, GraphOp, op, START, END, PARENT
        >>> @op
        ... def double(x: int):
        ...     return {"result": x * 2}
        >>> with GraphOp(name="test") as g:
        ...     step = double(x=PARENT["input"])
        ...     START >> step >> END
        >>> import asyncio
        >>> result = asyncio.run(Hush(g).run(inputs={"input": 5}))
        >>> result["result"]
        10
    """
```

### Phase 3.3: Set up doctests in CI

**Add to each package's `pyproject.toml`:**
```toml
[tool.pytest.ini_options]
addopts = "--doctest-modules"
```

**Or run separately:**
```bash
uv run pytest --doctest-modules python/hush-core/hush/core/engine.py
```

**CI workflow addition (`.github/workflows/tests.yaml`):**
```yaml
- name: Run doctests
  working-directory: python/${{ matrix.package }}
  run: uv run pytest --doctest-modules --tb=short -q
```

### Phase 3.4: Collapse architecture/ docs

**Keep (move to `docs/architecture/`):**

| Current file | New location | Rationale |
|-------------|-------------|-----------|
| `engine/execution-flow.md` | `docs/architecture/execution-flow.md` | Core design, can't be auto-generated |
| `state/overview.md` + `state/data-flow.md` | `docs/architecture/state-model.md` | Merge into one |
| (new) | `docs/architecture/streaming.md` | Streaming/generator design |
| (new) | `docs/architecture/rust-python-split.md` | Builder-executor split, serialization |

**Drop (redundant with docstrings + CLAUDE.md):**

| File | Why |
|------|-----|
| `ops/base-op.md` | Covered by `BaseOp` docstring |
| `ops/auto-naming.md` | Covered by `@op` / `@graph` docstring |
| `ops/creating-custom-op.md` | Covered by CLAUDE.md "Adding a New Op" section |
| `ops/graph-op.md` | Covered by `GraphOp` docstring |
| `ops/branch-op.md` | Covered by `BranchOp` docstring |
| `ops/iteration-ops.md` | Covered by `ForOp`/`WhileOp` docstrings |
| `ops/parser-op.md` | Covered by `ParserOp` docstring |
| `ops/exception-hierarchy.md` | Covered by `exceptions.py` docstrings |
| `state/state-schema.md` | Covered by `StateSchema` docstring |
| `state/memory-state.md` | Covered by `MemoryState` docstring |
| `state/indexer.md` | Covered by module docstring |
| `providers/llm-abstraction.md` | Covered by `BaseLLM` docstring |
| `providers/embedding-provider.md` | Covered by `BaseEmbedder` docstring |
| `providers/reranker-provider.md` | Covered by `BaseReranker` docstring |
| `providers/workflow-ops.md` | Covered by op docstrings |
| `providers/authentication.md` | Covered by auth module docstrings |
| `providers/adding-new-provider.md` | Covered by CLAUDE.md |
| `resources/resource-hub.md` | Covered by `ResourceHub` docstring |
| `resources/config-loading.md` | Covered by config module docstring |
| `resources/plugin-system.md` | Covered by plugin module docstring |
| `tracing/overview.md` | Covered by tracing module docstring |
| `tracing/data-model.md` | Covered by `TraceNode` docstring |
| `tracing/external-backends.md` | Covered by tracer docstrings |
| `ui-hush-eyes/overview.md` | Covered by hush-eyes CLAUDE.md |
| `ui-hush-eyes/api-and-storage.md` | Covered by hush-eyes CLAUDE.md |
| `contributing/development-setup.md` | Move to CONTRIBUTING.md |
| `contributing/code-style.md` | Move to CONTRIBUTING.md |
| `contributing/testing.md` | Move to CONTRIBUTING.md |
| `contributing/release-process.md` | Move to CONTRIBUTING.md |

**Result:** `architecture/` goes from ~30 files → 4 focused design docs.

### Phase 3.5: Examples (already moved in Phase 1.1)

Examples are already at `examples/` from Phase 1.1 (dissolved `tutorial/`).

**CI: run examples as smoke tests (non-integration ones):**
```yaml
- name: Run example smoke tests
  run: |
    for f in examples/01_hello_world.py examples/02_data_pipeline.py; do
      uv run python "$f"
    done
```

### Phase 3.6: GitHub Pages deployment

**New workflow: `.github/workflows/docs.yaml`**

```yaml
name: Docs

on:
  push:
    branches: [main]
    paths: [docs/**, python/**]

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv python install 3.10
      - run: uv pip install mkdocs-material mkdocstrings[python]
      - run: |
          cd python/hush-core && uv sync && cd ../..
          cd python/hush-providers && uv sync && cd ../..
          mkdocs build --strict
      - uses: actions/upload-pages-artifact@v3
        with:
          path: site/
      - uses: actions/deploy-pages@v4
```

### Phase 3.7: Trim CLAUDE.md files

After docstrings are written, CLAUDE.md files can be trimmed. Remove API details that are now in docstrings. Keep only:
- Module structure overview (directory tree)
- Key patterns and conventions
- "How to add X" recipes
- Gotchas / non-obvious behavior
- Cross-references to other packages

---

## Milestone 4: CI/CD — Publish Packages

### Phase 4.1: PyPI publishing (trusted publishing)

**One-time setup on pypi.org:**
- Register 4 packages: `hush-core`, `hush-providers`, `hush-telemetry`, `hush-serve`
- Add trusted publisher: repo `batman1m2001-cyber/Hush-ai`, workflow `publish.yaml`, environment `pypi`

**New workflow: `.github/workflows/publish.yaml`**

Trigger: push to `main` (with version check — only publish if version changed).

```yaml
name: Publish

on:
  push:
    branches: [main]

jobs:
  test:
    uses: ./.github/workflows/tests.yaml

  check-version:
    runs-on: ubuntu-latest
    outputs:
      changed: ${{ steps.check.outputs.changed }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 2
      - id: check
        run: |
          # Compare pyproject.toml versions with previous commit
          CHANGED="false"
          for pkg in python/hush-core python/hush-providers python/hush-telemetry python/hush-serve; do
            OLD=$(git show HEAD~1:$pkg/pyproject.toml 2>/dev/null | grep '^version' | head -1)
            NEW=$(grep '^version' $pkg/pyproject.toml | head -1)
            if [ "$OLD" != "$NEW" ]; then CHANGED="true"; fi
          done
          echo "changed=$CHANGED" >> "$GITHUB_OUTPUT"

  publish-pypi:
    needs: [test, check-version]
    if: needs.check-version.outputs.changed == 'true'
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    strategy:
      max-parallel: 1
      matrix:
        package: [hush-core, hush-providers, hush-telemetry, hush-serve]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv python install 3.10
      - run: cd python/${{ matrix.package }} && uv build
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: python/${{ matrix.package }}/dist/

  publish-crates:
    needs: [test, check-version]
    if: needs.check-version.outputs.changed == 'true'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - run: |
          cd rust
          cargo publish -p rush-providers --token ${{ secrets.CARGO_REGISTRY_TOKEN }}
          cargo publish -p rush-core --token ${{ secrets.CARGO_REGISTRY_TOKEN }}
          cargo publish -p rush-telemetry --token ${{ secrets.CARGO_REGISTRY_TOKEN }}
          cargo publish -p rush-serve --token ${{ secrets.CARGO_REGISTRY_TOKEN }}
```

### Phase 4.2: Update Cargo.toml for crates.io

Add `version` to path dependencies (required by crates.io):

```toml
# rust/rush-core/Cargo.toml
rush-providers = { path = "../rush-providers", version = "0.1.0" }

# rust/rush-serve/Cargo.toml
rush-core = { path = "../rush-core", version = "0.1.0" }
rush-providers = { path = "../rush-providers", version = "0.1.0" }
rush-telemetry = { path = "../rush-telemetry", version = "0.1.0" }

# rust/rush-telemetry/Cargo.toml
rush-core = { path = "../rush-core", version = "0.1.0" }
```

Add required metadata to all Cargo.toml:
```toml
license = "Apache-2.0"
repository = "https://github.com/batman1m2001-cyber/Hush-ai"
```

### Phase 4.3: Docs CI check

Add to `.github/workflows/tests.yaml`:
```yaml
  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install mkdocs-material mkdocstrings[python]
      - run: mkdocs build --strict  # fails on broken refs
```

---

## Execution Order

The phases have dependencies. Recommended order:

```
Phase 1.1–1.6  (project restructure)          ← do first, everything depends on paths
    ↓
Phase 3.1      (mkdocs setup)                  ← can start immediately after restructure
Phase 3.2      (write docstrings)              ← can start in parallel with 3.1
Phase 3.4      (collapse architecture/)        ← after 3.2 (docstrings replace arch docs)
Phase 3.5      (move examples)                 ← independent
Phase 3.3      (doctests CI)                   ← after 3.2
Phase 3.6      (GitHub Pages)                  ← after 3.1
Phase 3.7      (trim CLAUDE.md)                ← after 3.2
    ↓
Phase 2.1      (middleware system)             ← can start after 1.x
Phase 2.2      (engine.serve())                ← after 2.1
Phase 2.3      (run modes)                     ← after 2.1
Phase 2.4      (resource lifecycle)            ← after 2.1
Phase 2.5      (Rust mirror)                   ← after 2.1–2.4
    ↓
Phase 4.1–4.3  (CI/CD publish)                 ← after 1.x (paths settled)
```

**Suggested batching for PRs:**

| PR | Phases | Description |
|----|--------|-------------|
| PR 1 | 1.1–1.6 | Project restructure (big move, one PR to avoid partial state) |
| PR 2 | 3.1, 3.5, 3.6 | Docs infra (mkdocs, examples, GitHub Pages) |
| PR 3 | 3.2, 3.3, 3.4, 3.7 | Docstrings + collapse architecture |
| PR 4 | 2.1 | Middleware system |
| PR 5 | 2.2, 2.3 | engine.serve() + run modes |
| PR 6 | 2.4 | Resource lifecycle |
| PR 7 | 2.5 | Rust engine mirror |
| PR 8 | 4.1–4.3 | CI/CD publish pipeline |

---

## Final Structure (after all phases)

```
Hush-ai/
├── python/                     # Publishable Python packages only
│   ├── hush-core/              # Core engine, ops, state, tracing, middleware
│   ├── hush-providers/         # LLM, embedding, reranking providers
│   ├── hush-telemetry/         # External tracing backends
│   └── hush-serve/             # HTTP server (internal, exposed via engine.serve())
├── rust/                       # Publishable Rust crates only
│   ├── Cargo.toml              # Workspace root
│   ├── rush-core/              # Rust engine mirror
│   ├── rush-providers/         # Rust provider mirror
│   ├── rush-telemetry/         # Rust telemetry mirror
│   ├── rush-serve/             # Rust HTTP server
│   └── hush-eyes/              # Trace visualization
├── examples/                   # Runnable examples (from tutorial/examples/, tested in CI)
│   └── pyproject.toml          # Depends on python/hush-* packages
├── docs/                       # All documentation
│   ├── mkdocs.yml
│   ├── index.md
│   ├── api/                    # Auto-generated from docstrings
│   ├── guide/                  # Manual guide (from tutorial/docs/)
│   └── architecture/           # 4 core design docs (manual)
├── .github/workflows/
│   ├── tests.yaml              # Python + Rust tests + doctests + docs build
│   ├── format.yaml             # Ruff format/lint
│   ├── python-compatibility.yaml
│   ├── rust-runtime.yaml
│   ├── publish.yaml            # PyPI + crates.io (on version bump)
│   └── docs.yaml               # GitHub Pages deploy
├── MODULE_MAP.md               # Python ↔ Rust file mapping
├── CLAUDE.md                   # AI reference (trimmed)
├── CONTRIBUTING.md             # Dev setup, code style, testing
├── README.md
└── LICENSE
```
