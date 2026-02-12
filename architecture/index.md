# Hush Architecture

> Tai lieu nay danh cho core developers va AI assistants de hieu cach Hush hoat dong ben trong.

## Documentation System

Hush su dung ba lop documentation:

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: CLAUDE.md           Layer 2: architecture/            │
│  (Quick Reference)            (Deep Documentation)              │
│  ─────────────────            ────────────────────              │
│  • "How to add X"             • "Why X works this way"          │
│  • Patterns & examples        • Algorithms & diagrams           │
│  • For quick lookup           • For deep understanding          │
│  • Updated with API changes   • Updated with internals          │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3: hush-tutorial/                                        │
│  (User Guide - Vietnamese)                                      │
│  ─────────────────────────                                      │
│  • docs/  → User-facing documentation (00-12 chapters)          │
│  • examples/ → Runnable Python examples (01-15)                 │
│  • Updated when API/features change                             │
└─────────────────────────────────────────────────────────────────┘
```

### Khi nao dung gi?

| Can gi | Doc o dau |
|--------|-----------|
| Quick reference khi coding | `CLAUDE.md` trong package tuong ung |
| "Lam sao de them X?" | `CLAUDE.md` |
| "Tai sao X hoat dong nhu vay?" | `architecture/` (ban dang o day) |
| Deep dive internals | `architecture/` |
| Learning tu dau | `architecture/index.md` → reading order |
| User documentation (Vietnamese) | `hush-tutorial/docs/` |
| Runnable examples | `hush-tutorial/examples/` |
| Day nguoi khac dung Hush | `hush-tutorial/docs/00-tong-quan.md` → reading order |

### CLAUDE.md Files

| Package | CLAUDE.md | Noi dung |
|---------|-----------|----------|
| Root | [/CLAUDE.md](../CLAUDE.md) | Monorepo overview, conventions |
| hush-core | [/hush-core/CLAUDE.md](../hush-core/CLAUDE.md) | Op patterns, state management |
| hush-providers | [/hush-providers/CLAUDE.md](../hush-providers/CLAUDE.md) | Provider patterns |
| hush-ops | [/hush-ops/CLAUDE.md](../hush-ops/CLAUDE.md) | Tracer patterns |
| hush-tutorial | [/hush-tutorial/CLAUDE.md](../hush-tutorial/CLAUDE.md) | Doc conventions |
| hush-eyes | [/hush-eyes/CLAUDE.md](../hush-eyes/CLAUDE.md) | Extension patterns |

### Sync Rules

Khi thay doi code:

| Loai thay doi | CLAUDE.md | architecture/ | hush-tutorial/ |
|---------------|-----------|---------------|----------------|
| New op/provider/tracer | ✓ Usage pattern | ✓ Internals | ✓ docs/ + examples/ |
| API change | ✓ Update examples | ✓ Update explanations | ✓ Update docs + examples |
| Internal refactor (same API) | - | ✓ If algorithm changes | - |
| Bug fix | - | - | - |

Chi tiet sync mapping xem tai [/CLAUDE.md](../CLAUDE.md#hush-tutorial-sync-mapping).

## Tong quan he thong

```
┌─────────────────────────────────────────────────────────┐
│                      User Code                          │
│         (GraphOp, FuncOp, LLMOp, ...)             │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                    Hush Engine                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │ Compilation │  │  Execution  │  │  Scheduling │     │
│  └─────────────┘  └─────────────┘  └─────────────┘     │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                   State System                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │ StateSchema │  │ MemoryState │  │    Cell     │     │
│  └─────────────┘  └─────────────┘  └─────────────┘     │
└─────────────────────────────────────────────────────────┘
```

## Reading Order

### Level 1: Core Concepts

1. [Execution Flow](engine/execution-flow.md) - Workflow chay nhu the nao
2. [State Overview](state/overview.md) - State system basics
3. [BaseOp Anatomy](ops/base-op.md) - Cau truc mot op

### Level 2: Deep Dive

4. [Data Flow](state/data-flow.md) - Cach data di chuyen qua ops
5. [StateSchema](state/state-schema.md) - Schema design va indexing
6. [Graph Compilation](engine/compilation.md) - Build process
7. [Iteration Ops](ops/iteration-ops.md) - ForLoop, Map, While

### Level 3: Advanced

8. [ResourceHub](resources/resource-hub.md) - Resource management
9. [Plugin System](resources/plugin-system.md) - Plugin architecture
10. [Tracer System](tracing/tracer-interface.md) - Observability

## Quick Reference

### Muon hieu X hoat dong nhu the nao?

| Topic | File |
|-------|------|
| Workflow execution | [engine/execution-flow.md](engine/execution-flow.md) |
| Op scheduling | [engine/scheduling.md](engine/scheduling.md) |
| State management | [state/overview.md](state/overview.md) |
| Cell & multi-context | [state/memory-state.md](state/memory-state.md) |
| Index system | [state/indexer.md](state/indexer.md) |
| Op lifecycle | [ops/base-op.md](ops/base-op.md) |
| Nested graphs & @graph | [ops/graph-op.md](ops/graph-op.md) |
| Auto-naming (bytecode + source) | [ops/auto-naming.md](ops/auto-naming.md) |
| Loops (ForLoop, Map, While) | [ops/iteration-ops.md](ops/iteration-ops.md) |
| Conditional routing | [ops/branch-op.md](ops/branch-op.md) |
| LLM provider interface | [providers/llm-abstraction.md](providers/llm-abstraction.md) |
| Embedding provider | [providers/embedding-provider.md](providers/embedding-provider.md) |
| Reranker provider | [providers/reranker-provider.md](providers/reranker-provider.md) |
| Tracing system | [tracing/tracer-interface.md](tracing/tracer-interface.md) |
| SQLite storage | [tracing/local-tracer.md](tracing/local-tracer.md) |
| Database schema | [tracing/trace-data-model.md](tracing/trace-data-model.md) |
| Async buffer | [tracing/async-buffer.md](tracing/async-buffer.md) |

### Muon contribute/extend?

| Task | File |
|------|------|
| Tao custom op | [ops/creating-custom-op.md](ops/creating-custom-op.md) |
| Them LLM provider | [providers/adding-new-provider.md](providers/adding-new-provider.md) |
| Setup dev environment | [contributing/development-setup.md](contributing/development-setup.md) |
| Code style | [contributing/code-style.md](contributing/code-style.md) |
| Testing | [contributing/testing.md](contributing/testing.md) |
| Release process | [contributing/release-process.md](contributing/release-process.md) |

## Packages

| Package | Mo ta | Key Files | Quick Ref |
|---------|-------|-----------|-----------|
| hush-core | Core workflow engine | `engine.py`, `ops/`, `states/` | [CLAUDE.md](../hush-core/CLAUDE.md) |
| hush-providers | LLM/Embedding providers | `llms/base.py`, `embeddings/base.py` | [CLAUDE.md](../hush-providers/CLAUDE.md) |
| hush-ops | Tracing backends | `tracers/`, external integrations | [CLAUDE.md](../hush-ops/CLAUDE.md) |

## Folder Structure

```
architecture/
├── index.md                    ← Ban dang o day
│
├── engine/                     ← Core execution engine
│   ├── execution-flow.md       ← Workflow chay nhu the nao
│   ├── compilation.md          ← Graph compilation process
│   └── scheduling.md           ← Op scheduling & dependency
│
├── state/                      ← State management system
│   ├── overview.md             ← State system overview
│   ├── state-schema.md         ← StateSchema design
│   ├── memory-state.md         ← MemoryState implementation
│   ├── indexer.md              ← Index system internals
│   └── data-flow.md            ← Data flow through ops
│
├── ops/                      ← Op system
│   ├── base-op.md              ← BaseOp anatomy
│   ├── graph-op.md             ← Nested graphs, scoping & @graph
│   ├── auto-naming.md          ← Auto-naming (bytecode + source)
│   ├── iteration-ops.md        ← ForLoop, Map, While internals
│   ├── branch-op.md            ← Conditional routing
│   └── creating-custom-op.md   ← Guide tao op moi
│
├── resources/                  ← Resource management
│   ├── resource-hub.md         ← ResourceHub design
│   ├── plugin-system.md        ← Plugin architecture
│   └── config-loading.md       ← YAML parsing & env interpolation
│
├── tracing/                    ← Observability internals
│   ├── tracer-interface.md     ← BaseTracer abstract design
│   ├── local-tracer.md         ← SQLite implementation
│   ├── trace-data-model.md     ← Database schema
│   └── async-buffer.md         ← AsyncTraceBuffer design
│
├── providers/                  ← Provider system
│   ├── llm-abstraction.md      ← LLM provider interface
│   ├── embedding-provider.md   ← Embedding provider design
│   ├── reranker-provider.md    ← Reranker design
│   └── adding-new-provider.md  ← Guide them provider moi
│
└── contributing/               ← Contribution guides
    ├── development-setup.md    ← Setup dev environment
    ├── code-style.md           ← Coding conventions
    ├── testing.md              ← Testing strategy
    └── release-process.md      ← Release workflow
```

## Key Concepts

### Op Registration

Ops tu dong register voi parent graph qua `contextvars.ContextVar`:

```python
_current_graph: ContextVar[GraphOp] = ContextVar("current_graph")

class BaseOp:
    def __init__(self, ...):
        self.father = get_current()  # Auto-register voi parent
        if self.father:
            self.father._add_child(self)
```

### State Access Pattern

O(1) access qua pre-computed indices:

```python
# Compile time: build index map
_var_to_idx[("graph.op", "var")] = 5

# Runtime: direct array access
value = state._cells[5][context]
```

### Ref Resolution

Single-hop resolution cho data flow:

```python
# Pull ref: read tu source
inputs={"x": other_op["output"]}  # Pull 1 hop

# Push ref: write to target
outputs={"result": PARENT["output"]}  # Push 1 hop
```

### Non-blocking Tracing

Background process cho zero-latency impact:

```python
# Main thread: non-blocking enqueue
bg.write_trace(data)  # Returns immediately

# Background thread: actual write
def _worker_loop():
    msg = queue.get()
    self._insert_to_sqlite(msg)
```
