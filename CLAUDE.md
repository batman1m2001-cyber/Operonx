# Hush

Hush is a high-performance workflow engine that runs anything as a workflow—from IO-bound AI tasks like LLMs and agents to CPU-bound workloads needing native performance. Inspired by Airflow operators, Hush enforces clear, consistent coding conventions for building scalable workflows.

## Monorepo Structure

```
Hush-ai/
├── hush-core/          # Core workflow engine (ops, state, tracing)
├── hush-providers/     # LLM, embedding, reranking integrations
├── hush-telemetry/ # External tracing backends (Langfuse, OTEL)
├── hush-tutorial/      # Documentation (Vietnamese) and examples
├── hush-eyes/ # Standalone Rust server for trace visualization (Axum + SQLite)
├── architecture/       # Deep technical documentation
├── .github/            # CI/CD workflows, issue/PR templates
├── env.example         # Environment variables template
├── CONTRIBUTING.md     # Contributor guide
└── SECURITY.md         # Security policy
```

## Documentation System

```
┌─────────────────────────────────────────────────────────────────┐
│                      Documentation Layers                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Layer 1: CLAUDE.md (Quick Reference - for AI & developers)     │
│  ├── /CLAUDE.md              → Monorepo overview, conventions   │
│  ├── /hush-core/CLAUDE.md    → Core patterns, how to extend     │
│  ├── /hush-providers/CLAUDE.md → Provider patterns              │
│  ├── /hush-telemetry/CLAUDE.md → Tracer patterns            │
│  ├── /hush-tutorial/CLAUDE.md → Doc conventions                 │
│  └── /hush-eyes/CLAUDE.md → Rust server patterns     │
│                                                                  │
│  Layer 2: architecture/ (Deep Documentation - for learning)     │
│  ├── engine/      → Execution, compilation, scheduling          │
│  ├── state/       → StateSchema, MemoryState, indexer           │
│  ├── ops/       → Op internals, creating custom ops            │
│  ├── providers/   → Provider abstractions                       │
│  ├── resources/   → ResourceHub, plugin system                  │
│  ├── tracing/     → Tracer internals, data model                │
│  └── contributing/ → Dev setup, code style, testing             │
│                                                                  │
│  Layer 3: hush-tutorial/ (User Guide - for end users)           │
│  ├── docs/        → Vietnamese documentation (00-12 chapters)   │
│  └── examples/    → Runnable Python examples (01-15)            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### When to Use Each Layer

| Need | Go to |
|------|-------|
| Quick reference while coding | `CLAUDE.md` in relevant package |
| "How do I add X?" | `CLAUDE.md` |
| "Why does X work this way?" | `architecture/` |
| Deep dive into internals | `architecture/` |
| Learning from scratch | `architecture/index.md` → reading order |
| User-facing docs (Vietnamese) | `hush-tutorial/docs/` |
| Runnable examples | `hush-tutorial/examples/` |
| Teaching someone Hush | `hush-tutorial/docs/00-tong-quan.md` → reading order |

## Documentation Update Rules

### When to Update What

| Change Type | CLAUDE.md | architecture/ | hush-tutorial/ |
|-------------|-----------|---------------|----------------|
| New op type | ✓ How to use | ✓ How it works internally | ✓ Add to docs/03 + example |
| New provider | ✓ Integration pattern | ✓ Abstraction design | ✓ Add to docs/04 or 06 + example |
| New tracer | ✓ Usage pattern | ✓ Implementation details | ✓ Add to docs/09 + example |
| API change | ✓ Update examples | ✓ Update explanations | ✓ Update docs + examples |
| Internal refactor (same API) | - | ✓ If algorithm changes | - |
| Bug fix | - | - | - |
| Adding tests | - | - | - |

### Sync Rules

When making significant changes:
1. Update CLAUDE.md with new patterns/examples
2. Update architecture/ with detailed explanations
3. **Update hush-tutorial/docs/ with user-facing documentation (Vietnamese)**
4. **Update/add hush-tutorial/examples/ with runnable examples**
5. Ensure cross-references are correct
6. Verify code examples match actual API

### hush-tutorial Sync Mapping

| Code Location | docs/ Update | examples/ Update |
|---------------|--------------|------------------|
| hush-core/ops/ | 03-core-concepts.md | 01-02, 05 |
| hush-core/engine.py | 03-core-concepts.md | 01-02 |
| hush-providers/llm/ | 04-llm-integration.md | 03-04 |
| hush-providers/embedding/ | 06-embeddings-rag.md | 07, 14 |
| hush-providers/reranker/ | 06-embeddings-rag.md | 07, 14 |
| hush-core/tracing/ | 09-tracing-observability.md | 06 |
| hush-telemetry/tracers/ | 09-tracing-observability.md | 08, 09 |
| Control flow (For/While/Branch) | 05-loops-branches.md | 05 |
| Error handling | 07-error-handling.md | 10 |
| Parallel patterns | 08-parallel-execution.md | 13 |
| Agent patterns | 10-agent-workflow.md | 11 |
| Multi-model | 11-multi-model.md | 12 |

### Cross-Reference Convention

**In CLAUDE.md** → link to architecture/ for deep dives:
```markdown
For details on state indexing, see [architecture/state/indexer.md](architecture/state/indexer.md)
```

**In architecture/** → note that CLAUDE.md has quick patterns:
```markdown
For quick usage patterns, see the package's CLAUDE.md file.
```

## Package Dependencies

```
hush-core (foundation - no hush dependencies)
    ↓
hush-providers (depends on hush-core)
    ↓
hush-telemetry (depends on hush-core)
```

## When to Modify Which Package

| Task | Package |
|------|---------|
| New op type | hush-core/hush/core/ops/ |
| New LLM/embedding/reranker provider | hush-providers/hush/providers/ |
| New tracing backend | hush-telemetry/hush/telemetry/ |
| Documentation or examples | hush-tutorial/ |
| Trace visualization server | hush-eyes/ |

## Global Coding Conventions

### Python (hush-core, hush-providers, hush-telemetry)

- **Python**: 3.10+
- **Async-first**: All I/O operations use asyncio
- **Formatter**: Ruff (Black-compatible) with 100 char line length
- **Linter**: Ruff with rules E, F, I, W
- **Type hints**: Use typing module, Pydantic for validation
- **Testing**: pytest + pytest-asyncio, `asyncio_mode = "auto"`

### Rust (hush-eyes)

- Build with cargo (`cargo build --release`)
- Axum HTTP framework, rusqlite for SQLite storage
- CLI via clap (--host, --port, --db-path)

### Code Style

- Base classes go in `base.py`
- Configuration classes go in `config.py` (Pydantic models)
- Factory functions go in `factory.py`
- Each module has `__init__.py` with explicit exports

### Fix Bugs at the Root, Never Patch Around Them

When a core API doesn't work as expected (e.g., `op >> END` not auto-forwarding outputs), **fix the root cause** in the core code. Never add workaround calls like `op._setup_schema()` or other private-method hacks in user-facing code, examples, tests, or decorators. If something that should "just work" doesn't, the fix belongs in the internal implementation (e.g., `__exit__`, `build()`, `__rrshift__`), not in a wrapper that papers over the gap.

## Build & Test Commands

```bash
# hush-core
cd hush-core && uv pip install -e ".[dev]" && pytest

# hush-providers
cd hush-providers && uv pip install -e ".[dev]" && pytest

# hush-telemetry
cd hush-telemetry && uv pip install -e ".[dev]" && pytest

# hush-eyes (Rust trace server)
cd hush-eyes && cargo build --release
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
| Tests | `.github/workflows/tests.yaml` | Pytest for all packages |
| Python Compatibility | `.github/workflows/python-compatibility.yaml` | Python 3.10-3.12 matrix |

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

**Always use `Op.of()` classmethods** for concise op creation. Use explicit keyword arguments — never positional args:

```python
# CORRECT
chat = ChainOp.of(resource="gpt-4o", template={"system": "...", "user": "{q}"}, q=PARENT["q"])
llm = LLMOp.of(resource="gpt-4o", messages=PARENT["msgs"])
embed = EmbeddingOp.of(resource="bge-m3", texts=PARENT["texts"])

# WRONG — no positional args
chat = ChainOp.of("gpt-4o", {"system": "...", "user": "{q}"}, q=PARENT["q"])
```

### Edge Types
- `>>` : Hard edge (sequential, counts toward ready_count)
- `>>~` or `>` : Soft edge (conditional, for branch outputs)

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

## Exception Hierarchy

All op errors inherit from `OpError` in `hush-core/hush/core/exceptions.py`:
- `ParserError`, `CodeError`, `BranchError`, `ConditionError`, `IterationError`
- `PromptError`, `EmbeddingError`, `RerankError`

## Deep Documentation Links

For detailed explanations, see [architecture/](architecture/):

| Topic | Quick (CLAUDE.md) | Deep (architecture/) |
|-------|-------------------|---------------------|
| Execution flow | - | [engine/execution-flow.md](architecture/engine/execution-flow.md) |
| Auto-naming | hush-core/CLAUDE.md | [ops/auto-naming.md](architecture/ops/auto-naming.md) |
| State system | hush-core/CLAUDE.md | [state/overview.md](architecture/state/overview.md) |
| Op internals | hush-core/CLAUDE.md | [ops/base-op.md](architecture/ops/base-op.md) |
| Creating ops | hush-core/CLAUDE.md | [ops/creating-custom-op.md](architecture/ops/creating-custom-op.md) |
| Adding providers | hush-providers/CLAUDE.md | [providers/adding-new-provider.md](architecture/providers/adding-new-provider.md) |

## Local Development with uv

Packages use editable installs via uv.sources in pyproject.toml:
```toml
[tool.uv.sources]
hush-core = { path = "../hush-core", editable = true }
```
