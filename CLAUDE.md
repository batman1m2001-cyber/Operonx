# Hush

Hush is a high-performance workflow engine that runs anything as a workflow—from IO-bound AI tasks like LLMs and agents to CPU-bound workloads needing native performance. Inspired by Airflow operators, Hush enforces clear, consistent coding conventions for building scalable workflows.

## Monorepo Structure

```
Hush-ai/
├── python/                # Python packages
│   ├── hush-icore/        # Core workflow engine (ops, state, tracing)
│   ├── hush-providers/    # LLM, embedding, reranking integrations
│   ├── hush-serve/        # HTTP API server (FastAPI + uvicorn)
│   └── hush-telemetry/    # External tracing backends (Langfuse, OTEL)
├── rust/                  # Rust crates
│   ├── Cargo.toml         # Workspace root
│   ├── hush-icore/        # High-performance execution backend (pure rlib, DashMap)
│   ├── hush-providers/    # Native HTTP providers, ONNX inference
│   ├── hush-serve/        # Standalone HTTP server (Axum + hush-icore)
│   ├── hush-telemetry/    # Rust telemetry backends
│   └── hush-eyes/         # Trace visualization server (Axum + SQLite)
├── examples/              # Runnable Python examples
├── docs/                  # All documentation
│   ├── guide/             # User guide (Vietnamese, 00-13 chapters)
│   ├── api/               # Auto-generated API reference (mkdocstrings)
│   └── architecture/      # 4 core design docs
├── .github/               # CI/CD workflows, issue/PR templates
├── env.example            # Environment variables template
├── CONTRIBUTING.md        # Contributor guide
└── SECURITY.md            # Security policy
```

## Documentation

| Layer | Location | Purpose |
|-------|----------|---------|
| .claude/rules/ | Path-scoped | Per-package module maps, patterns, gotchas (loaded on demand) |
| .claude/skills/ | On-demand | Repeatable workflows: /publish, /bench, /example |
| docs/api/ | Auto-generated | API reference from docstrings (mkdocstrings) |
| docs/architecture/ | 4 design docs | Execution flow, state model, streaming, Rust-Python split |
| docs/guide/ | Vietnamese (00-13) | User-facing tutorial |
| examples/ | Runnable Python (01-15) | Learning by example |

## Package Dependencies

```
python/hush-icore (foundation - no hush dependencies)
    ↓
python/hush-providers (depends on hush-icore)
    ↓
python/hush-telemetry (depends on hush-icore)
    ↓
python/hush-serve (depends on hush-icore, optional: hush-providers, hush-telemetry)

rust/hush-icore (Pure Rust engine - standalone rlib, built via cargo build)
rust/hush-providers (Rust crate - used by hush-icore, built via cargo build)
rust/hush-serve (Rust binary - depends on hush-icore + hush-providers)
```

## When to Modify Which Package

| Task | Package |
|------|---------|
| New op type | python/hush-icore/hush/core/ops/ |
| New LLM/embedding/reranker provider (Python) | python/hush-providers/hush/providers/ |
| New LLM/embedding/reranker provider (Rust) | rust/hush-providers/src/ |
| New tracing backend | python/hush-telemetry/hush/telemetry/ |
| Rust execution backend | rust/hush-icore/src/ |
| HTTP API server (Python) | python/hush-serve/hush/serve/ |
| HTTP API server (Rust) | rust/hush-serve/src/ |
| New Rust plugin op | Create cdylib crate with `hush_plugin!` macro, reference via `@op(rust="./crate::module::func")` |
| Documentation or examples | docs/guide/ + examples/ |
| Trace visualization server | rust/hush-eyes/ |

## Global Coding Conventions

### Python (hush-icore, hush-providers, hush-telemetry)

- **Python**: 3.10+
- **Async-first**: All I/O operations use asyncio
- **Formatter**: Ruff (Black-compatible) with 100 char line length
- **Linter**: Ruff with rules E, F, I, W
- **Type hints**: Use typing module, Pydantic for validation
- **Testing**: pytest + pytest-asyncio, `asyncio_mode = "auto"`

### Rust (rust/hush-icore, rust/hush-providers, rust/ui-hush-eyes)

- **hush-icore**: Pure Rust library crate (`rlib`), built via `cd rust && cargo build --release`
  - DashMap for concurrent state, rayon for parallel execution
  - Standalone engine: `Hush::new(json_str)` + `Hush::run_json(inputs)`
- **hush-providers**: Rust crate with per-provider modules (llms/, embeddings/, rerankers/)
  - Native HTTP providers (OpenAI, Azure, Gemini, Cohere, Pinecone, vLLM)
  - ONNX inference via `ort` crate
  - Built as part of hush-icore via `cargo build`
- **ui-hush-eyes**: Standalone binary, built via `cargo build --release`
  - Axum HTTP framework, rusqlite for SQLite storage
  - CLI via clap (--host, --port, --db-path)

### Rust Plugin Ops (cdylib)

All Rust ops are dispatched via the `OpRegistry` trait in `hush-icore/src/registry.rs`. Custom ops are written in cdylib crates and loaded at runtime by hush-serve via `libloading`.

```python
# Reference plugin op by crate::module::function path
@op(rust="./rust_ops::pipeline::double")
def double(x: int):
    return {"result": x * 2}  # Python fallback
```

**Adding a new Rust op:**
1. Write a `fn(&serde_json::Value) -> serde_json::Value` function in your cdylib crate
2. Export via `hush_plugin!(func_name)` macro (from `hush-plugin` crate)
3. Reference via `@op(rust="./crate::module::func")`

### Naming Conventions

**Packages** follow the `hush-*` pattern:
- `hush-icore` — Core engine (Python + Rust)
- `hush-providers` — Provider integrations (Python + Rust)
- `hush-telemetry` — Tracing backends (Python + Rust)
- `hush-serve` — HTTP server (Python + Rust)
- `hush-plugin` — Rust plugin SDK

### Code Style

- Base classes go in `base.py`
- Configuration classes go in `config.py` (Pydantic models)
- Factory functions go in `factory.py`
- Each module has `__init__.py` with explicit exports

### Fix Bugs at the Root, Never Patch Around Them

When a core API doesn't work as expected (e.g., `op >> END` not auto-forwarding outputs), **fix the root cause** in the core code. Never add workaround calls like `op._setup_schema()` or other private-method hacks in user-facing code, examples, tests, or decorators. If something that should "just work" doesn't, the fix belongs in the internal implementation (e.g., `__exit__`, `build()`, `__rrshift__`), not in a wrapper that papers over the gap.

## Build & Test Commands

**IMPORTANT:** Always use `uv run` to execute Python tools (pytest, ruff, etc.) — never call them directly.

```bash
# hush-icore
cd python/hush-icore && uv pip install -e ".[dev]" && uv run -m pytest

# hush-providers
cd python/hush-providers && uv pip install -e ".[dev]" && uv run -m pytest

# hush-telemetry
cd python/hush-telemetry && uv pip install -e ".[dev]" && uv run -m pytest

# hush-serve (Python HTTP server)
cd python/hush-serve && uv sync --all-extras && uv run -m pytest

# hush-icore (Rust execution backend)
cd rust && cargo test -p hush-icore

# hush-providers (Rust provider crate — built with hush-icore, tests are Rust-only)
cd rust && cargo test -p hush-providers

# All Rust crates (workspace)
cd rust && cargo test --workspace

# hush-serve (Rust HTTP server)
cd rust && cargo build -p hush-serve --release

# ui-hush-eyes (Rust trace server)
cd rust && cargo build -p hush-eyes --release
```

## Development Workflow

### Environment Setup

Copy `env.example` to `.env` and fill in your API keys:
```bash
cp env.example .env
# Edit .env with your OPENAI_API_KEY, LANGFUSE_* keys, etc.
```

The `.env` file is **not** auto-loaded by `Operon(graph)`. Call `operon.bootstrap()` at process startup to load `.env` and `resources.yaml` — see [Resource Setup](#resource-setup-bootstrap--resourcehub) below.

### Pre-commit Hooks

Auto-format and lint on every commit:
```bash
# Install hooks (one-time setup)
pre-commit install

# Run manually on all files
pre-commit run --all-files
```

### CI/CD (GitHub Actions)

Workflows run automatically on every PR:

| Workflow | File | Purpose |
|----------|------|---------|
| Format & Lint | `.github/workflows/format.yaml` | Ruff format/lint check |
| Tests | `.github/workflows/tests.yaml` | Pytest for all packages + example smoke tests + docs build |
| Python Compatibility | `.github/workflows/python-compatibility.yaml` | Python 3.10-3.12 matrix |
| Rust Runtime | `.github/workflows/rust-runtime.yaml` | Rust workspace build + tests |
| Docs | `.github/workflows/docs.yaml` | Build mkdocs + deploy to GitHub Pages |
| Publish | `.github/workflows/publish.yaml` | PyPI + crates.io (on version bump) |

### Git Commits

**IMPORTANT:** Before making any commit, always verify and set the correct git identity:

```bash
# Check current identity
git config user.name && git config user.email

# Must be set to:
git config user.name "Bruce Win"
git config user.email "batman1m2001@gmail.com"
```

**Commit rules:**
- **Always** commit as "Bruce Win <batman1m2001@gmail.com>"
- **Never** add "Co-Authored-By: Claude" or any AI co-author lines
- **Never** commit as any other identity

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for full contributor guide, including:
- Development setup
- Code style requirements
- PR workflow
- Documentation update rules

## Key Patterns

### Resource Setup (`bootstrap` + `ResourceHub`)

Provider ops (`LLMOp`, `EmbeddingOp`, `RerankOp`, telemetry tracers) resolve credentials and model configs through the global `ResourceHub`. The hub is **not** installed automatically — you must set it up before constructing an `Operon` engine that uses any provider op.

**Convenience (covers 95% of cases):**

```python
import operon
from operon.core import Operon

operon.bootstrap()                  # loads ./.env + ./resources.yaml from CWD
engine = Operon(graph)
```

**Explicit path (notebooks, multi-config, tests):**

```python
import operon
operon.bootstrap(resources="configs/prod.yaml")  # also loads ./.env unless env=False
```

**Pure-compute graphs need no setup at all** — `Operon(graph)` works hub-free if the graph doesn't reference any resource by name:

```python
from operon.core import Operon, GraphOp, op, START, END, PARENT

@op
def double(x: int):
    return {"result": x * 2}

with GraphOp(name="pure") as graph:
    step = double(x=PARENT["x"], outputs={"result": PARENT["result"]})
    START >> step >> END

result = await Operon(graph).run(inputs={"x": 5})  # no .env, no resources.yaml needed
```

**Behavior reference:**

| Situation | What happens |
|---|---|
| `bootstrap()`, no `./resources.yaml` | `ResourceHubWarning` named "No resources.yaml found at ..." — pure compute still works; provider ops will fail at op resolution. |
| `bootstrap()`, `${VAR}` referenced but unset | `ResourceHubWarning` listing every unset var and the resource that uses it. Setting the var before `engine.run()` resolves it. |
| `Operon(graph)` with provider op, no hub installed | `RuntimeError("ResourceHub not initialized. ...")` at engine init (eager warmup). |
| `hub.get("llm:gpt-4o")` with key not present | `KeyError` listing source path and available keys. |
| `hub.get(key)` with `${VAR}` still unset at resolve time | `EnvVarUnsetError` (subclass of `RuntimeError`) naming the var, source path, and `.env` paths searched. |

**Key invariants:**

- `Operon(graph)` does **not** load `.env` or `resources.yaml`. It does not clobber a pre-installed hub. It is a pure orchestrator.
- `ResourceHub.set_instance(hub)` is authoritative — `bootstrap()` and `auto()` are idempotent and respect a hub that's already installed.
- Run from any CWD — `bootstrap(resources="absolute/path.yaml")` decouples setup from working directory.

### Op Definition
```python
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op
def double(x: int):
    return {"result": x * 2}

with GraphOp(name="workflow") as graph:
    step = double(x=PARENT["input"])
    START >> step >> END
```

### @graph — Modular Workflows

Turn a builder function into a reusable GraphOp factory with auto-naming:

```python
from hush.core import graph, op, START, END, PARENT

@op
def detect_card(conversation: str):
    return {"has_card": "card" in conversation}

@graph
def verify_card(conversation):
    check = detect_card(conversation=conversation)
    START >> check >> END

# Use like a function call — auto-named from variable
with GraphOp(name="main") as graph:
    v = verify_card(conversation=PARENT["conv"])
    START >> v >> END  # v.name == "v"
```

- Function params → `PARENT` refs (injected automatically)
- Supports `name=`, `outputs=`, `description=` kwargs
- `>> END` auto-forwarding works (outputs pre-populated via `_setup_schema()`)

### Shorthand Style Rule

**Always use `Op.of()` classmethods** for concise op creation, and `chain()` for prompt+LLM combos. Use explicit keyword arguments — never positional args:

```python
# CORRECT
chat = chain(resource="gpt-4o", template={"system": "...", "user": "{q}"}, q=PARENT["q"])
llm = LLMOp.of(resource="gpt-4o", messages=PARENT["msgs"])
embed = EmbeddingOp.of(resource="bge-m3", texts=PARENT["texts"])

# WRONG — no positional args
chat = chain("gpt-4o", {"system": "...", "user": "{q}"}, q=PARENT["q"])
```

### Edge Types
- `>>` : Hard edge (sequential, counts toward ready_count)
- `>>~` : Soft edge (conditional, for branch outputs)

### State References — PARENT vs op["key"]

**Rule: Use `op["key"]` to pass data between sibling ops. Use `PARENT["key"]` only for external inputs (from `engine.run()` or from the parent graph in nested contexts).**

```python
# CORRECT — read from sibling op's output
g = greet(name=PARENT["name"])       # PARENT["name"] = external input
u = upper(text=g["greeting"])        # g["greeting"] = sibling op output
START >> g >> u >> END

# WRONG — PARENT["greeting"] doesn't exist, g didn't forward there
u = upper(text=PARENT["greeting"])   # ✗ greeting is in g's state, not parent
```

- `PARENT["key"]` : External inputs from `engine.run(inputs={...})` or parent graph
- `op["key"]` : Output from a sibling op within the same graph
- `>> END` : Auto-forwards the last op's outputs to graph result
- `outputs={"content": PARENT["answer"]}` : Explicit output mapping (inline, for renaming keys)
- `op["src"] >> PARENT["dest"]` : Output mapping via `>>` operator (standalone, same effect)

### Output Mapping with `>>`

Use `op["key"] >> PARENT["key"]` to map an op's output to the parent graph state. This is equivalent to `outputs={"key": PARENT["dest"]}` but more readable for selective forwarding:

```python
# Style 1: outputs= parameter (inline with op creation)
llm = LLMOp.of(resource="gpt-4o", messages=p["messages"], outputs={"content": PARENT["answer"]})

# Style 2: >> operator (standalone line, equivalent)
llm = LLMOp.of(resource="gpt-4o", messages=p["messages"])
llm["content"] >> PARENT["answer"]

# Common in loops — forward loop outputs or update loop state
process["new_messages"] >> PARENT["messages"]
loop["final_answer"] >> PARENT["answer"]

# Wildcard — forward all outputs
step = process(x=PARENT["x"], outputs={"*": PARENT})
```

### Iteration Patterns (ForOp/MapOp/WhileOp removed)

The old `ForOp`, `MapOp`, `Each` classes were replaced by two patterns:

**1. Generator ops (replaces ForOp/MapOp)** — use `yield` to iterate:
```python
@op
def each_item(items: list):
    for item in items:
        yield {"value": item}

@op
def double(value: int):
    return {"result": value * 2}

with GraphOp(name="iterate") as graph:
    gen = each_item(items=PARENT["numbers"])
    step = double(value=gen["value"])
    START >> gen >> step >> END
# Downstream ops run in parallel per yield (streaming scheduler default)
```

**2. `GraphOp.loop()` / `@graph.loop()` (replaces WhileOp)** — feedback loops:
```python
with GraphOp.loop(until="count >= 5", count=0) as loop:
    inc = increment(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]
    START >> inc >> END
```

## Exception Hierarchy

All op errors inherit from `OpError` in `python/hush-icore/hush/core/exceptions.py`:
- `ParserError`, `CodeError`, `BranchError`, `ConditionError`, `IterationError`
- `PromptError`, `EmbeddingError`, `RerankError`

## Deep Documentation Links

| Topic | File |
|-------|------|
| Execution flow | [docs/architecture/execution-flow.md](docs/architecture/execution-flow.md) |
| State model | [docs/architecture/state-model.md](docs/architecture/state-model.md) |
| Streaming | [docs/architecture/streaming.md](docs/architecture/streaming.md) |
| Rust-Python split | [docs/architecture/rust-python-split.md](docs/architecture/rust-python-split.md) |

## Local Development with uv

Packages use editable installs via uv.sources in pyproject.toml:
```toml
[tool.uv.sources]
hush-icore = { path = "../hush-icore", editable = true }
```
