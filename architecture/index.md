# Hush Architecture

> Tài liệu này dành cho core developers và AI assistants để hiểu cách Hush hoạt động bên trong.

## Tổng quan hệ thống

```
┌─────────────────────────────────────────────────────────┐
│                      User Code                          │
│         (GraphNode, CodeNode, LLMNode, ...)             │
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
3. [BaseNode Anatomy](nodes/base-node.md) - Cấu trúc một node

### Level 2: Deep Dive

4. [Data Flow](state/data-flow.md) - Cách data di chuyển qua nodes
5. [StateSchema](state/state-schema.md) - Schema design và indexing
6. [Graph Compilation](engine/compilation.md) - Build process
7. [Iteration Nodes](nodes/iteration-nodes.md) - ForLoop, Map, While

### Level 3: Advanced

8. [ResourceHub](resources/resource-hub.md) - Resource management
9. [Plugin System](resources/plugin-system.md) - Plugin architecture
10. [Tracer System](tracing/tracer-interface.md) - Observability

## Quick Reference

### Muốn hiểu X hoạt động như thế nào?

| Topic | File |
|-------|------|
| Workflow execution | [engine/execution-flow.md](engine/execution-flow.md) |
| Node scheduling | [engine/scheduling.md](engine/scheduling.md) |
| State management | [state/overview.md](state/overview.md) |
| Cell & multi-context | [state/memory-state.md](state/memory-state.md) |
| Index system | [state/indexer.md](state/indexer.md) |
| Node lifecycle | [nodes/base-node.md](nodes/base-node.md) |
| Nested graphs | [nodes/graph-node.md](nodes/graph-node.md) |
| Loops (ForLoop, Map, While) | [nodes/iteration-nodes.md](nodes/iteration-nodes.md) |
| Conditional routing | [nodes/branch-node.md](nodes/branch-node.md) |
| LLM provider interface | [providers/llm-abstraction.md](providers/llm-abstraction.md) |
| Embedding provider | [providers/embedding-provider.md](providers/embedding-provider.md) |
| Reranker provider | [providers/reranker-provider.md](providers/reranker-provider.md) |
| Tracing system | [tracing/tracer-interface.md](tracing/tracer-interface.md) |
| SQLite storage | [tracing/local-tracer.md](tracing/local-tracer.md) |
| Database schema | [tracing/trace-data-model.md](tracing/trace-data-model.md) |
| Async buffer | [tracing/async-buffer.md](tracing/async-buffer.md) |

### Muốn contribute/extend?

| Task | File |
|------|------|
| Tạo custom node | [nodes/creating-custom-node.md](nodes/creating-custom-node.md) |
| Thêm LLM provider | [providers/adding-new-provider.md](providers/adding-new-provider.md) |
| Setup dev environment | [contributing/development-setup.md](contributing/development-setup.md) |
| Code style | [contributing/code-style.md](contributing/code-style.md) |
| Testing | [contributing/testing.md](contributing/testing.md) |
| Release process | [contributing/release-process.md](contributing/release-process.md) |

## Packages

| Package | Mô tả | Key Files |
|---------|-------|-----------|
| hush-core | Core workflow engine | `engine.py`, `nodes/`, `states/` |
| hush-providers | LLM/Embedding providers | `llms/base.py`, `embeddings/base.py` |
| hush-observability | Tracing backends | `tracers/`, external integrations |

## Folder Structure

```
architecture/
├── index.md                    ← Bạn đang ở đây
│
├── engine/                     ← Core execution engine
│   ├── execution-flow.md       ← Workflow chạy như thế nào
│   ├── compilation.md          ← Graph compilation process
│   └── scheduling.md           ← Node scheduling & dependency
│
├── state/                      ← State management system
│   ├── overview.md             ← State system overview
│   ├── state-schema.md         ← StateSchema design
│   ├── memory-state.md         ← MemoryState implementation
│   ├── indexer.md              ← Index system internals
│   └── data-flow.md            ← Data flow through nodes
│
├── nodes/                      ← Node system
│   ├── base-node.md            ← BaseNode anatomy
│   ├── graph-node.md           ← Nested graphs & scoping
│   ├── iteration-nodes.md      ← ForLoop, Map, While internals
│   ├── branch-node.md          ← Conditional routing
│   └── creating-custom-node.md ← Guide tạo node mới
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
│   └── adding-new-provider.md  ← Guide thêm provider mới
│
└── contributing/               ← Contribution guides
    ├── development-setup.md    ← Setup dev environment
    ├── code-style.md           ← Coding conventions
    ├── testing.md              ← Testing strategy
    └── release-process.md      ← Release workflow
```

## Key Concepts

### Node Registration

Nodes tự động register với parent graph qua `contextvars.ContextVar`:

```python
_current_graph: ContextVar[GraphNode] = ContextVar("current_graph")

class BaseNode:
    def __init__(self, ...):
        self.father = get_current()  # Auto-register với parent
        if self.father:
            self.father._add_child(self)
```

### State Access Pattern

O(1) access qua pre-computed indices:

```python
# Compile time: build index map
_var_to_idx[("graph.node", "var")] = 5

# Runtime: direct array access
value = state._cells[5][context]
```

### Ref Resolution

Single-hop resolution cho data flow:

```python
# Pull ref: read từ source
inputs={"x": other_node["output"]}  # Pull 1 hop

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
