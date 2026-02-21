# Hush Architecture

> Tài liệu này dành cho core developers và AI assistants để hiểu cách Hush hoạt động bên trong.

## Documentation System

Hush sử dụng ba lớp documentation:

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

### Khi nào dùng gì?

| Cần gì | Đọc ở đâu |
|--------|-----------|
| Quick reference khi coding | `CLAUDE.md` trong package tương ứng |
| "Làm sao để thêm X?" | `CLAUDE.md` |
| "Tại sao X hoạt động như vậy?" | `architecture/` (bạn đang ở đây) |
| Deep dive internals | `architecture/` |
| Learning từ đầu | `architecture/index.md` → reading order |
| User documentation (Vietnamese) | `hush-tutorial/docs/` |
| Runnable examples | `hush-tutorial/examples/` |
| Dạy người khác dùng Hush | `hush-tutorial/docs/00-tong-quan.md` → reading order |

### CLAUDE.md Files

| Package | CLAUDE.md | Nội dung |
|---------|-----------|----------|
| Root | [/CLAUDE.md](../CLAUDE.md) | Monorepo overview, conventions |
| hush-core | [/hush-core/CLAUDE.md](../hush-core/CLAUDE.md) | Op patterns, state management |
| hush-providers | [/hush-providers/CLAUDE.md](../hush-providers/CLAUDE.md) | Provider patterns |
| hush-telemetry | [/hush-telemetry/CLAUDE.md](../hush-telemetry/CLAUDE.md) | Tracer patterns |
| hush-tutorial | [/hush-tutorial/CLAUDE.md](../hush-tutorial/CLAUDE.md) | Doc conventions |
| hush-eyes | [/hush-eyes/CLAUDE.md](../hush-eyes/CLAUDE.md) | Extension patterns |
| rush-core | [/rush-core/CLAUDE.md](../rush-core/CLAUDE.md) | Rust backend patterns |

### Sync Rules

Khi thay đổi code:

| Loại thay đổi | CLAUDE.md | architecture/ | hush-tutorial/ |
|---------------|-----------|---------------|----------------|
| New op/provider/tracer | ✓ Usage pattern | ✓ Internals | ✓ docs/ + examples/ |
| API change | ✓ Update examples | ✓ Update explanations | ✓ Update docs + examples |
| Internal refactor (same API) | - | ✓ If algorithm changes | - |
| Bug fix | - | - | - |

Chi tiết sync mapping xem tại [/CLAUDE.md](../CLAUDE.md#hush-tutorial-sync-mapping).

## Tổng quan hệ thống

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

1. [Execution Flow](engine/execution-flow.md) - Workflow chạy như thế nào
2. [State Overview](state/overview.md) - State system basics
3. [BaseOp Anatomy](ops/base-op.md) - Cấu trúc một op

### Level 2: Deep Dive

4. [Data Flow](state/data-flow.md) - Cách data di chuyển qua ops
5. [StateSchema](state/state-schema.md) - Schema design và indexing
6. [Graph Compilation](engine/compilation.md) - Build process
7. [Iteration Ops](ops/iteration-ops.md) - ForLoop, Map, While

### Level 3: Advanced

8. [ResourceHub](resources/resource-hub.md) - Resource management
9. [Plugin System](resources/plugin-system.md) - Plugin architecture
10. [Tracer System](tracing/overview.md) - Observability
11. [Streaming System](streams/streaming-system.md) - Real-time data streaming
12. [Trace Data Model](tracing/data-model.md) - Trace data structures
13. [External Backends](tracing/external-backends.md) - Langfuse & OTEL
14. [Hush Eyes Server](hush-eyes/overview.md) - Standalone Rust server for trace visualization
15. [Rush-Core Backend](../rush-core/CLAUDE.md) - Rust execution engine (DashMap, rayon, batch parallel)

## Quick Reference

### Muốn hiểu X hoạt động như thế nào?

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
| ParserOp (LLM output parsing) | [ops/parser-op.md](ops/parser-op.md) |
| Exception hierarchy | [ops/exception-hierarchy.md](ops/exception-hierarchy.md) |
| Streaming system | [streams/streaming-system.md](streams/streaming-system.md) |
| LLM provider interface | [providers/llm-abstraction.md](providers/llm-abstraction.md) |
| Embedding provider | [providers/embedding-provider.md](providers/embedding-provider.md) |
| Reranker provider | [providers/reranker-provider.md](providers/reranker-provider.md) |
| Provider workflow ops | [providers/workflow-ops.md](providers/workflow-ops.md) |
| Authentication | [providers/authentication.md](providers/authentication.md) |
| Tracing system | [tracing/overview.md](tracing/overview.md) |
| Trace data model | [tracing/data-model.md](tracing/data-model.md) |
| External tracing backends | [tracing/external-backends.md](tracing/external-backends.md) |
| Hush Eyes server | [hush-eyes/overview.md](hush-eyes/overview.md) |
| Hush Eyes API & storage | [hush-eyes/api-and-storage.md](hush-eyes/api-and-storage.md) |

### Muốn contribute/extend?

| Task | File |
|------|------|
| Tạo custom op | [ops/creating-custom-op.md](ops/creating-custom-op.md) |
| Thêm LLM provider | [providers/adding-new-provider.md](providers/adding-new-provider.md) |
| Setup dev environment | [contributing/development-setup.md](contributing/development-setup.md) |
| Code style | [contributing/code-style.md](contributing/code-style.md) |
| Testing | [contributing/testing.md](contributing/testing.md) |
| Release process | [contributing/release-process.md](contributing/release-process.md) |

## Packages

| Package | Mô tả | Key Files | Quick Ref |
|---------|-------|-----------|-----------|
| hush-core | Core workflow engine | `engine.py`, `ops/`, `states/` | [CLAUDE.md](../hush-core/CLAUDE.md) |
| hush-providers | LLM/Embedding providers | `llms/base.py`, `embeddings/base.py` | [CLAUDE.md](../hush-providers/CLAUDE.md) |
| hush-telemetry | Tracing backends | `tracers/`, external integrations | [CLAUDE.md](../hush-telemetry/CLAUDE.md) |
| hush-eyes | Standalone Rust server for trace visualization | `src/main.rs`, `src/api/`, `src/db/` | [CLAUDE.md](../hush-eyes/CLAUDE.md) |
| rush-core | High-performance Rust execution backend | `src/engine.rs`, `src/ops/`, `src/states/` | [CLAUDE.md](../rush-core/CLAUDE.md) |

## Folder Structure

```
architecture/
├── index.md                    ← Bạn đang ở đây
│
├── engine/                     ← Core execution engine
│   ├── execution-flow.md       ← Workflow chạy như thế nào
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
├── streams/                    ← Real-time data streaming
│   └── streaming-system.md     ← BaseStreamingService, InMemoryStreamService
│
├── ops/                        ← Op system
│   ├── base-op.md              ← BaseOp anatomy
│   ├── graph-op.md             ← Nested graphs, scoping & @graph
│   ├── auto-naming.md          ← Auto-naming (bytecode + source)
│   ├── iteration-ops.md        ← ForLoop, Map, While internals
│   ├── branch-op.md            ← Conditional routing
│   ├── parser-op.md            ← ParserOp (LLM output → structured data)
│   ├── exception-hierarchy.md  ← OpError exception system
│   └── creating-custom-op.md   ← Guide tạo op mới
│
├── resources/                  ← Resource management
│   ├── resource-hub.md         ← ResourceHub design
│   ├── plugin-system.md        ← Plugin architecture
│   └── config-loading.md       ← YAML parsing & env interpolation
│
├── tracing/                    ← Observability internals
│   ├── overview.md             ← Tracer, TraceCollector, FlushWorker
│   ├── data-model.md           ← Trace data structures
│   ├── external-backends.md    ← Langfuse & OTEL integration
│   └── refactor-plan.md        ← Migration notes
│
├── providers/                  ← Provider system
│   ├── llm-abstraction.md      ← LLM provider interface
│   ├── embedding-provider.md   ← Embedding provider design
│   ├── reranker-provider.md    ← Reranker design
│   ├── workflow-ops.md         ← LLMOp, ChainOp, PromptOp, EmbeddingOp, RerankOp
│   ├── authentication.md       ← Keycloak auth provider
│   └── adding-new-provider.md  ← Guide thêm provider mới
│
├── hush-eyes/                  ← Standalone Rust server for trace visualization
│   ├── overview.md             ← Server architecture, CLI, module structure
│   └── api-and-storage.md      ← REST API endpoints, SQLite schema, data models
│
└── contributing/               ← Contribution guides
    ├── development-setup.md    ← Setup dev environment
    ├── code-style.md           ← Coding conventions
    ├── testing.md              ← Testing strategy
    └── release-process.md      ← Release workflow
```

## Key Concepts

### Op Registration

Ops tự động register với parent graph qua `contextvars.ContextVar`:

```python
_current_graph: ContextVar[GraphOp] = ContextVar("current_graph")

class BaseOp:
    def __init__(self, ...):
        self.father = get_current()  # Auto-register với parent
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
# Pull ref: đọc từ source
inputs={"x": other_op["output"]}  # Pull 1 hop

# Push ref: ghi đến target
outputs={"result": PARENT["output"]}  # Push 1 hop
```

### Non-blocking Tracing

FlushWorker chạy trong ThreadPoolExecutor, không block main thread:

```python
# After engine.run() completes:
FlushWorker.submit(tracers, graph, state)  # Returns immediately

# In background thread:
trace_data = TraceCollector.collect(graph, state)
tracer.flush(trace_data)  # HTTP POST, SDK calls, etc.
```
