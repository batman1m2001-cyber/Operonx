# Hush

Hush is a high-performance workflow engine that runs anything as a workflow—from IO-bound AI tasks like LLMs and agents to CPU-bound workloads needing native performance. Inspired by Airflow operators, Hush enforces clear, consistent coding conventions for building scalable workflows.

## Monorepo Structure

```
Hush-ai/
├── python/                # Python packages
│   ├── hush-core/         # Core workflow engine (ops, state, tracing)
│   ├── hush-providers/    # LLM, embedding, reranking integrations
│   ├── hush-serve/        # HTTP API server (FastAPI + uvicorn)
│   └── hush-telemetry/    # External tracing backends (Langfuse, OTEL)
├── rust/                  # Rust crates
│   ├── Cargo.toml         # Workspace root
│   ├── rush-core/         # High-performance execution backend (pure rlib, DashMap)
│   ├── rush-providers/    # Native HTTP providers, ONNX inference
│   ├── rush-serve/        # Standalone HTTP server (Axum + rush-core)
│   ├── rush-telemetry/    # Rust telemetry backends
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
| CLAUDE.md | Per-package | Quick reference, conventions, recipes |
| docs/api/ | Auto-generated | API reference from docstrings (mkdocstrings) |
| docs/architecture/ | 4 design docs | Execution flow, state model, streaming, Rust-Python split |
| docs/guide/ | Vietnamese (00-13) | User-facing tutorial |
| examples/ | Runnable Python (01-20) | Learning by example |

## Package Dependencies

```
python/hush-core (foundation - no hush dependencies)
    ↓
python/hush-providers (depends on hush-core)
    ↓
python/hush-telemetry (depends on hush-core)
    ↓
python/hush-serve (depends on hush-core, optional: hush-providers, hush-telemetry)

rust/rush-core (Pure Rust engine - standalone rlib, built via cargo build)
rust/rush-providers (Rust crate - used by rush-core, built via cargo build)
rust/rush-serve (Rust binary - depends on rush-core + rush-providers)
```

## When to Modify Which Package

| Task | Package |
|------|---------|
| New op type | python/hush-core/hush/core/ops/ |
| New LLM/embedding/reranker provider (Python) | python/hush-providers/hush/providers/ |
| New LLM/embedding/reranker provider (Rust) | rust/rush-providers/src/ |
| New tracing backend | python/hush-telemetry/hush/telemetry/ |
| Rust execution backend | rust/rush-core/src/ |
| HTTP API server (Python) | python/hush-serve/hush/serve/ |
| HTTP API server (Rust) | rust/rush-serve/src/ |
| New built-in Rust op | rust/rush-core/src/builtin_ops/ops.rs + dispatch in rust/rush-core/src/builtin_ops/mod.rs |
| Documentation or examples | docs/guide/ + examples/ |
| Trace visualization server | rust/hush-eyes/ |

## Global Coding Conventions

### Python (hush-core, hush-providers, hush-telemetry)

- **Python**: 3.10+
- **Async-first**: All I/O operations use asyncio
- **Formatter**: Ruff (Black-compatible) with 100 char line length
- **Linter**: Ruff with rules E, F, I, W
- **Type hints**: Use typing module, Pydantic for validation
- **Testing**: pytest + pytest-asyncio, `asyncio_mode = "auto"`

### Rust (rust/rush-core, rust/rush-providers, rust/ui-hush-eyes)

- **rush-core**: Pure Rust library crate (`rlib`), built via `cd rust && cargo build --release`
  - DashMap for concurrent state, rayon for parallel execution
  - Standalone engine: `Rush::new(json_str)` + `Rush::run_json(inputs)`
- **rush-providers**: Rust crate with per-provider modules (llms/, embeddings/, rerankers/)
  - Native HTTP providers (OpenAI, Azure, Gemini, Cohere, Pinecone, vLLM)
  - ONNX inference via `ort` crate
  - Built as part of rush-core via `cargo build`
- **ui-hush-eyes**: Standalone binary, built via `cargo build --release`
  - Axum HTTP framework, rusqlite for SQLite storage
  - CLI via clap (--host, --port, --db-path)

### Rust Built-in Ops

Built-in Rust ops live in `rust/rush-core/src/builtin_ops/` as an internal module. Dispatch is handled via a match statement in `rust/rush-core/src/builtin_ops/mod.rs` -- no dynamic loading, no C ABI.

```python
# Reference the Rust op by function name
@op(rust="double")
def double(x: int):
    return {"result": x * 2}  # Python fallback
```

**Adding a new built-in op:**
1. Write a `fn(&serde_json::Value) -> serde_json::Value` function in `rust/rush-core/src/builtin_ops/ops.rs`
2. Add a match arm in `rust/rush-core/src/builtin_ops/mod.rs` to dispatch to it
3. Reference via `@op(rust="func_name")`

### Naming Conventions

**Packages** follow the `hush-*` / `rush-*` pattern:
- `hush-*` — Python packages (hush-core, hush-providers, hush-telemetry)
- `rush-*` — Rust packages (rush-core, rush-providers)

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
# hush-core
cd python/hush-core && uv pip install -e ".[dev]" && uv run -m pytest

# hush-providers
cd python/hush-providers && uv pip install -e ".[dev]" && uv run -m pytest

# hush-telemetry
cd python/hush-telemetry && uv pip install -e ".[dev]" && uv run -m pytest

# hush-serve (Python HTTP server)
cd python/hush-serve && uv sync --all-extras && uv run -m pytest

# rush-core (Rust execution backend)
cd rust && cargo test -p rush-core

# rush-providers (Rust provider crate — built with rush-core, tests are Rust-only)
cd rust && cargo test -p rush-providers

# All Rust crates (workspace)
cd rust && cargo test --workspace

# rush-serve (Rust HTTP server)
cd rust && cargo build -p rush-serve --release

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

All op errors inherit from `OpError` in `python/hush-core/hush/core/exceptions.py`:
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
hush-core = { path = "../hush-core", editable = true }
```
