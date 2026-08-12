# Operonx

Operonx is a high-performance workflow engine that runs anything as a workflow — from IO-bound AI tasks like LLMs and agents to CPU-bound workloads needing native performance. Inspired by Airflow operators, it enforces clear, consistent conventions for building scalable async workflows.

## Repository Structure

Single Python package. The Rust execution backend lives in a sibling repo,
[operonx-rs](https://github.com/batman1m2001-cyber/operonx-rs); this repo
is Python-only.

```
Operonx/
├── operonx/                       # Python package (pip install operonx)
│   ├── core/                      # Engine, ops, state, tracing, registry
│   │   ├── engine.py              # Operon class (pure orchestrator)
│   │   ├── ops/                   # Op base classes, flow, transform, graph
│   │   ├── states/                # State containers, schema
│   │   ├── workflow_trace.py      # V3 WorkflowTrace + OpExecution
│   │   ├── registry/              # ResourceHub, storage, plugin registry
│   │   ├── configs/               # Pydantic config models
│   │   └── exceptions.py          # OpError hierarchy
│   ├── providers/                 # LLM, embedding, reranker, ONNX backends
│   │   ├── ops/                   # LLMOp, EmbeddingOp, RerankOp, VectorSearchOp, DocFetchOp
│   │   ├── llms/                  # OpenAI, Azure, Gemini, Anthropic, vLLM
│   │   ├── embeddings/            # vLLM, TEI, HuggingFace, ONNX
│   │   ├── rerankers/             # vLLM, TEI, HuggingFace, ONNX, Pinecone
│   │   ├── auth/                  # Keycloak token provider
│   │   └── registry/              # Plugin registrations to core ResourceHub
│   └── telemetry/                 # V3 Consumers — local, Langfuse, OTEL
│       └── consumers/             # LocalConsumer, LangfuseConsumer
├── examples/python/               # Runnable examples (ex01..ex15)
├── tests/
│   ├── internal/                  # Backend-specific unit tests
│   └── spec/                      # JSON-fixture tests (mirrored in operonx-rs)
├── docs/                          # mkdocs site
├── pyproject.toml                 # Python package definition
├── mkdocs.yml                     # Material theme + mkdocstrings
├── env.example                    # Environment variables template
├── resources.yaml                 # Resource configuration template (optional)
├── CONTRIBUTING.md                # Contributor guide
├── CHANGELOG.md                   # Keep-a-Changelog format
├── CODE_OF_CONDUCT.md             # Contributor Covenant 2.1
└── SECURITY.md                    # Security policy
```

## Documentation

| Layer | Location | Purpose |
|-------|----------|---------|
| docs/architecture/ | mkdocs site | Internals: execution flow, state model, streaming, observability, resource-hub, failure modes |
| docs/guide/ | mkdocs site | User-facing tutorial — installation through deployment |
| docs/api/ | Auto-generated | API reference from docstrings (mkdocstrings) |
| docs/design/ | mkdocs site | Design records and archives — history, not current behaviour |
| examples/python/ | Runnable Python | Learning by example (ex01..ex15) |
| .claude/skills/ | On-demand | Repeatable workflows: /publish, /bench, /example |

### Which doc answers which question

Open the one that matches the question. Do not read the plan documents for
current behaviour — they describe intent, and several rows in them were
measured to be wrong.

| Question | File |
|---|---|
| How do contexts and cancellation work? | `docs/architecture/execution-flow.md` |
| Which `stream()` mode sees which ops? | `docs/architecture/streaming.md` |
| What reaches the trace? `exclude=`, `observe_max`? | `docs/architecture/observability.md` |
| **What mistakes does this codebase keep making?** | `docs/architecture/failure-modes.md` |
| Is this plausible claim about operonx actually true? | `docs/design/AGENT_PLAN_ARCHIVE.md` |
| What changed, and what breaks on upgrade? | `CHANGELOG.md` |
| What is the agent layer for, and what is left? | `AGENT_EXTENSION_PLAN.md` §0 |
| Should this belong in `operonx/agents/`? | `operonx/agents/CONTRIBUTING.md` |

**Read `failure-modes.md` before a non-trivial fix.** Nine recurring
shapes, each with the measurement that proved it — most of them cost a
shipped defect to learn. The shortest version: every one of those defects
returned a *plausible value* rather than raising, so ask what a caller
receives when your new code path fails.

## Package Dependencies

```
operonx.core              (foundation — no operonx siblings)
    ↓
operonx.providers         (depends on operonx.core)
    ↓
operonx.telemetry         (depends on operonx.core)
```

Rust runtime code lives in the sibling repo
[operonx-rs](https://github.com/batman1m2001-cyber/operonx-rs) and is not
touched from this repo.

## When to Modify Which Area

| Task | Location |
|------|----------|
| New op type | [operonx/core/ops/](operonx/core/ops/) |
| New LLM/embedding/reranker provider | [operonx/providers/](operonx/providers/) |
| New tracing consumer | [operonx/telemetry/consumers/](operonx/telemetry/consumers/) |
| HTTP API server | `operonx[serve]` — FastAPI module under [operonx/serve/](operonx/serve/) |
| Rust runtime work | [operonx-rs](https://github.com/batman1m2001-cyber/operonx-rs) (separate repo) |
| Documentation | [docs/](docs/) — guide + architecture + api |
| Examples | [examples/python/](examples/python/) |

## Coding Conventions

### Python

- **Python**: 3.10+
- **Async-first**: All I/O operations use asyncio
- **Formatter**: Ruff (Black-compatible) with 100 char line length
- **Linter**: Ruff with rules E, F, I, W
- **Type hints**: Use typing module, Pydantic for validation
- **Testing**: pytest + pytest-asyncio, `asyncio_mode = "auto"`

### Naming Conventions

- **Python package**: single `operonx` namespace.
- **PyPI extras**: `operonx[anthropic]`, `operonx[onnx]`, `operonx[langfuse]`, `operonx[otel]`, `operonx[serve]`, `operonx[standard]`, `operonx[all]`.

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
# Python — single sync, single test invocation
uv sync --all-extras
uv run pytest tests/ -m "not integration"

# Python with coverage
uv run pytest tests/ --cov=operonx --cov-report=xml -m "not integration"

# Docs
uv run mkdocs serve            # local preview
uv run mkdocs build --strict   # CI build
```

## Development Workflow

### Environment Setup

Copy `env.example` to `.env` and fill in your API keys:

```bash
cp env.example .env
# Edit .env with your OPENAI_API_KEY, LANGFUSE_* keys, etc.
```

The `.env` file is **not** auto-loaded by `Operon(graph)`. Call `operonx.bootstrap()` at process startup to load `.env` and `resources.yaml` — see [Resource Setup](#resource-setup-bootstrap--resourcehub) below.

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
| Format & Lint | [.github/workflows/format.yaml](.github/workflows/format.yaml) | Ruff format/lint check |
| Tests | [.github/workflows/tests.yaml](.github/workflows/tests.yaml) | Pytest + extras-smoke matrix + example smoke tests |
| Python Compatibility | [.github/workflows/python-compatibility.yaml](.github/workflows/python-compatibility.yaml) | Python 3.10/3.11/3.12 matrix |
| Docs | [.github/workflows/docs.yaml](.github/workflows/docs.yaml) | mkdocs build --strict + deploy to GitHub Pages |
| Publish | [.github/workflows/publish.yaml](.github/workflows/publish.yaml) | PyPI (on version bump) |

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
import operonx
from operonx.core import Operon

operonx.bootstrap()                  # loads ./.env + ./resources.yaml from CWD
engine = Operon(graph)
```

**Explicit path (notebooks, multi-config, tests):**

```python
import operonx
operonx.bootstrap(resources="configs/prod.yaml")  # also loads ./.env unless env=False
```

**Pure-compute graphs need no setup at all** — `Operon(graph)` works hub-free if the graph doesn't reference any resource by name:

```python
from operonx.core import Operon, GraphOp, op, START, END, PARENT

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
| `hub.get(key)` with `${VAR}` still unset at resolve time | `EnvVarUnsetError` (subclass of `KeyError`) naming the var, source path, and `.env` paths searched. |

**Key invariants:**

- `Operon(graph)` does **not** load `.env` or `resources.yaml`. It does not clobber a pre-installed hub. It is a pure orchestrator.
- `ResourceHub.set_instance(hub)` is authoritative — `bootstrap()` and `auto()` are idempotent and respect a hub that's already installed.
- Run from any CWD — `bootstrap(resources="absolute/path.yaml")` decouples setup from working directory.

Full reference: [docs/architecture/resource-hub.md](docs/architecture/resource-hub.md).

### Op Definition

```python
from operonx.core import Operon, GraphOp, op, START, END, PARENT

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
from operonx.core import graph, op, START, END, PARENT, GraphOp

@op
def detect_card(conversation: str):
    return {"has_card": "card" in conversation}

@graph
def verify_card(conversation):
    check = detect_card(conversation=conversation)
    START >> check >> END

# Use like a function call — auto-named from variable
with GraphOp(name="main") as g:
    v = verify_card(conversation=PARENT["conv"])
    START >> v >> END  # v.name == "v"
```

- Function params → `PARENT` refs (injected automatically)
- Supports `name=`, `outputs=`, `description=` kwargs
- `>> END` auto-forwarding works (outputs pre-populated via `_setup_schema()`)

### Shorthand Style Rule

**Always use `Op.of()` classmethods** for concise op creation, and `chat()` (or `ask()`) for prompt+LLM combos. Use explicit keyword arguments — never positional args:

```python
# CORRECT
c = chat(resource="gpt-4o", template={"system": "...", "user": "{q}"}, q=PARENT["q"])
llm = LLMOp.of(resource="gpt-4o", messages=PARENT["msgs"])
embed = EmbeddingOp.of(resource="bge-m3", texts=PARENT["texts"])

# WRONG — no positional args
c = chat("gpt-4o", {"system": "...", "user": "{q}"}, q=PARENT["q"])
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
u = upper(text=PARENT["greeting"])   # greeting is in g's state, not parent
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

### Iteration Patterns

The classic `ForOp` / `MapOp` / `WhileOp` classes were replaced by two patterns:

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

All op errors inherit from `OpError` in [operonx/core/exceptions.py](operonx/core/exceptions.py):
- `ParserError`, `CodeError`, `BranchError`, `ConditionError`, `IterationError`
- `PromptError`, `EmbeddingError`, `RerankError`

Resource-hub errors live in [operonx/core/registry/](operonx/core/registry/):
- `EnvVarUnsetError` (subclass of `KeyError`) — `${VAR}` interpolation failure at resolve time
- `ResourceHubWarning` — `warnings` category for missing `resources.yaml` and unset `${VAR}` at bootstrap

## Deep Documentation Links

| Topic | File |
|-------|------|
| Architecture overview | [docs/architecture/overview.md](docs/architecture/overview.md) |
| Execution flow | [docs/architecture/execution-flow.md](docs/architecture/execution-flow.md) |
| State model | [docs/architecture/state-model.md](docs/architecture/state-model.md) |
| Streaming | [docs/architecture/streaming.md](docs/architecture/streaming.md) |
| Resource hub | [docs/architecture/resource-hub.md](docs/architecture/resource-hub.md) |
| Guide (Installation → Deployment) | [docs/guide/](docs/guide/) |

## Local Development with uv

The repository is a single editable install:

```bash
uv sync --all-extras
```

Optional extras can also be installed individually for debugging:

```bash
uv pip install -e ".[anthropic]"
uv pip install -e ".[onnx]"
uv pip install -e ".[serve]"
```
