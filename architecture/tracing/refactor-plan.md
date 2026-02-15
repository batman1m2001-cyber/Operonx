# Tracing Refactor Plan

## Problem

The current tracing architecture has three issues:

1. **Mixed concerns in MemoryState** — `record_trace()`, `_trace_store`, `_execution_count`, `_tags` live inside the state object that should only handle workflow data flow.

2. **SQLite as intermediate buffer** — Trace data is structured in memory, destructured into flat SQL rows, written to SQLite via a background process + IPC, then reconstructed back into structured data for flushing. Wasted work.

3. **Redundant data per run** — Static node metadata (`op_type`, `parent_name`, `contain_generation`) is recorded on every execution, even though it's fixed at graph compile time. Only inputs/outputs and timing are truly per-run.

## Current Flow (to be replaced)

```
Op.run()
  → MemoryState.record_trace()
    → build dict (static + dynamic data mixed)
    → BackgroundProcess.enqueue()
      → deque buffer → drain thread → IPC queue
        → worker_loop() in separate OS process
          → write_traces_batch() → SQLite INSERT (status='writing')
          → mark_complete() → status='pending'
          → fetch_pending() → rebuild_flush_data() → dispatch_flush()
            → LangfuseTracer.flush() / OTELTracer.flush()

hush-eyes (VS Code extension) → sql.js WASM → reads traces.db directly
```

## New Architecture

```
Op.run()
  → TraceCollector.record(op_name, ctx, inputs, outputs, timing, usage)
    → list.append()  (in-memory, ~0.001ms)

engine.run() completes
  → flush_worker.submit(tracers, trace_data)
    → ThreadPoolExecutor → each tracer.flush(trace_data) in parallel
        ├→ HushEyesTracer  → POST http://localhost:8420/api/ingest
        ├→ LangfuseTracer  → POST to Langfuse API
        └→ OTELTracer      → POST to OTEL endpoint

hush-eyes (Rust standalone server)
  → POST /api/ingest   → stores in traces.db (SQLite)
  → GET  /api/traces   → query API
  → GET  /             → serves web UI (HTML/CSS/JS)
  → open http://localhost:8420 in any browser
```

### Key Principles

- **Engine knows nothing about storage** — no SQLite, no background process, no IPC
- **Tracers are independent backends** — hush-eyes, Langfuse, OTEL don't know about each other
- **hush-eyes owns all trace storage** — SQLite lives in hush-eyes, not in hush-core
- **Static data captured once** — graph structure extracted at compile time, merged at flush
- **In-memory collection** — zero overhead during execution, flush after run completes
- **Non-blocking flush** — ThreadPoolExecutor, all tracers flush in parallel

---

## Folder Structures

### hush-eyes (Rust standalone server — REPLACES current VS Code extension)

```
hush-eyes/
├── Cargo.toml                      # Rust project manifest
├── README.md
├── CLAUDE.md
│
├── src/
│   ├── main.rs                     # CLI entry point (clap)
│   │                                 - `hush-eyes serve --port 8420`
│   │                                 - `hush-eyes serve --db ./traces.db`
│   │
│   ├── config.rs                   # Server config (port, db path, cors)
│   │
│   ├── db/
│   │   ├── mod.rs                  # SQLite connection pool + init
│   │   ├── schema.rs               # CREATE TABLE migrations
│   │   ├── write.rs                # INSERT traces (from ingest API)
│   │   └── read.rs                 # SELECT queries (for query API)
│   │                                 - list traces (paginated, filtered)
│   │                                 - get trace detail (by request_id)
│   │                                 - get db info (size, trace count)
│   │                                 - delete traces
│   │
│   ├── api/
│   │   ├── mod.rs                  # Axum router setup
│   │   ├── ingest.rs               # POST /api/ingest
│   │   │                             - Receives trace_data from engine
│   │   │                             - Merges graph_structure + records
│   │   │                             - Writes to SQLite
│   │   │
│   │   ├── query.rs                # GET  /api/traces          (list, paginated)
│   │   │                             # GET  /api/traces/:id     (detail by request_id)
│   │   │                             # GET  /api/db-info        (db path, size, count)
│   │   │                             # DELETE /api/traces       (clear all)
│   │   │                             # DELETE /api/traces/:id   (clear one)
│   │   │
│   │   └── models.rs               # serde request/response structs
│   │                                 - IngestRequest
│   │                                 - TraceListResponse
│   │                                 - TraceDetailResponse
│   │                                 - DbInfoResponse
│   │
│   └── static/                     # Embedded web UI (served at /)
│       ├── index.html              # Adapted from current webview/index.html
│       ├── main.js                 # Adapted from current webview/main.js
│       │                             - Replace vscode.postMessage → fetch()
│       │                             - Replace message listener → async functions
│       └── styles.css              # Keep as-is from current webview/styles.css
│
├── resources/                      # Icons (keep for branding)
│   └── icon.png
│
└── tests/
    ├── ingest_test.rs              # Test ingest API
    └── query_test.rs               # Test query API
```

**Delete entirely (current hush-eyes files):**

```
src/extension.ts            # VS Code entry point
src/tracePanel.ts           # VS Code WebviewPanel wrapper
src/traceViewProvider.ts    # VS Code sidebar wrapper
src/database.ts             # sql.js WASM SQLite reader
tsconfig.json               # TypeScript config
package.json                # Node.js manifest
package-lock.json           # Node.js lockfile
install.sh                  # VS Code extension installer
install.ps1                 # VS Code extension installer (Windows)
.vscodeignore               # VS Code packaging ignore
webview/hush-icon-16.png    # VS Code tab icon
webview/hush-icon-20.png    # VS Code activity bar icon
resources/hush-activity-bar.svg  # VS Code sidebar icon
```

**Rust dependencies (Cargo.toml):**

```toml
[dependencies]
axum = "0.8"                # Web framework
tokio = { version = "1", features = ["full"] }  # Async runtime
rusqlite = { version = "0.32", features = ["bundled"] }  # SQLite
serde = { version = "1", features = ["derive"] }  # Serialization
serde_json = "1"            # JSON
clap = { version = "4", features = ["derive"] }  # CLI args
tower-http = { version = "0.6", features = ["cors", "fs"] }  # Static files + CORS
tracing = "0.1"             # Logging
tracing-subscriber = "0.3"  # Log output
```

### hush-core tracing (Python — new modules)

```
hush-core/hush/core/
│
├── tracing/                        # NEW PACKAGE — replaces background/ + tracers/
│   ├── __init__.py                 # Exports: TraceCollector, FlushWorker, BaseTracer
│   │
│   ├── collector.py                # TraceCollector
│   │                                 - Created per engine.run()
│   │                                 - _extract_structure(graph) → static graph metadata
│   │                                 - record() → list.append(TraceRecord)
│   │                                 - build_trace_data() → merge static + dynamic
│   │
│   ├── models.py                   # Data classes
│   │                                 - TraceRecord (per-op dynamic data)
│   │                                 - NodeStructure (per-node static data)
│   │                                 - TraceData (flush-ready payload)
│   │
│   ├── flush_worker.py             # FlushWorker
│   │                                 - Global singleton ThreadPoolExecutor(max_workers=4)
│   │                                 - submit(tracers, trace_data)
│   │                                 - atexit shutdown (drain pending flushes)
│   │
│   └── base.py                     # BaseTracer (simplified)
│                                     - flush(trace_data) → abstract static method
│                                     - No flush_in_background, no _merge_tags
│                                     - No _get_tracer_config, no _insert_legacy_traces
│
├── engine.py                       # MODIFIED
│   │                                 - Create TraceCollector (if tracers provided)
│   │                                 - Pass collector to graph.run()
│   │                                 - After run: flush_worker.submit(tracers, trace_data)
│   │
├── states/
│   └── state.py                    # MODIFIED — remove all tracing fields
│                                     - Delete: _trace_store, _trace_metadata,
│                                       _execution_order, _execution_count, _tags
│                                     - Delete: record_trace(), record_execution(),
│                                       has_trace_store, trace_metadata, execution_order,
│                                       tags, add_tag()
│                                     - Keep: schema, _cells, _user_id, _session_id,
│                                       _request_id
│
├── ops/
│   ├── base.py                     # MODIFIED — call collector.record()
│   ├── graph/graph_op.py           # MODIFIED — pass collector, call collector.record()
│   └── iteration/base.py           # MODIFIED — pass collector, call collector.record()
│
│
│  ┌─── DELETE ────────────────────────────────────────────────────┐
│  │                                                               │
│  │  background/                   # ENTIRE PACKAGE               │
│  │  ├── __init__.py               # get_background, DEFAULT_DB   │
│  │  ├── process.py                # BackgroundProcess, IPC, drain│
│  │  ├── worker.py                 # worker_loop, subprocess      │
│  │  ├── db.py                     # SQLite buffer ops            │
│  │  └── flush.py                  # rebuild_flush_data, dispatch │
│  │                                                               │
│  │  tracers/                      # REPLACED by tracing/         │
│  │  ├── store.py                  # TraceStore (dead wrapper)    │
│  │  └── local.py                  # LocalTracer (→ hush-eyes)    │
│  │                                                               │
│  └──────────────────────────────────────────────────────────────┘
│
│  NOTE: tracers/base.py → moves to tracing/base.py
│        tracers/media.py → evaluate if still needed
│        tracers/__init__.py → replaced by tracing/__init__.py
```

### hush-providers (minimal change)

```
hush-providers/hush/providers/
└── ops/
    └── llm.py                      # MODIFIED — call collector.record()
                                      instead of state.record_trace()
```

### hush-telemetry (simplify flush interface)

```
hush-telemetry/hush/telemetry/
├── __init__.py                     # Keep
├── plugin.py                       # Keep (ResourceHub registration)
│
├── backends/                       # Keep as-is
│   ├── langfuse/
│   │   ├── config.py               # LangfuseConfig (Pydantic)
│   │   ├── client.py               # LangfuseClient
│   │   └── __init__.py
│   └── otel/
│       ├── config.py               # OTELConfig (Pydantic)
│       ├── client.py               # OTELClient
│       └── __init__.py
│
└── tracers/
    ├── __init__.py
    ├── langfuse.py                 # MODIFIED — simplify flush()
    │                                 - Receives structured trace_data directly
    │                                 - No more rebuild_flush_data dependency
    │                                 - Inherits from tracing.BaseTracer
    │
    └── otel.py                     # MODIFIED — same simplification
```

---

## Component Design

### TraceCollector (hush-core/hush/core/tracing/collector.py)

Created per `engine.run()`. Separates static (graph structure) from dynamic (per-op records).

```python
@dataclass
class NodeStructure:
    """Static metadata for a graph node. Captured once at compile time."""
    op_name: str
    op_type: str
    parent_name: Optional[str]
    contain_generation: bool

@dataclass
class TraceRecord:
    """Dynamic data for a single op execution."""
    op_name: str
    context_id: Optional[str]
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    start_time: str           # ISO format
    end_time: str             # ISO format
    duration_ms: float
    model: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    cost: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None


class TraceCollector:
    def __init__(self, graph: GraphOp, request_id: str, user_id: str, session_id: str):
        self._request_id = request_id
        self._user_id = user_id
        self._session_id = session_id
        self._workflow_name = graph.name
        self._graph_structure = self._extract_structure(graph)
        self._records: List[TraceRecord] = []
        self._tags: List[str] = []

    def _extract_structure(self, graph: GraphOp) -> List[NodeStructure]:
        """Walk compiled graph, capture static metadata for each node."""
        ...

    def record(self, op_name, context_id, inputs, outputs,
               start_time, end_time, duration_ms, **kwargs):
        """Append a trace record. Called by ops during execution."""
        self._records.append(TraceRecord(
            op_name=op_name, context_id=context_id,
            inputs=inputs, outputs=outputs,
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            duration_ms=duration_ms, **kwargs
        ))

    def add_tag(self, tag: str):
        if tag not in self._tags:
            self._tags.append(tag)

    def build_trace_data(self) -> Dict[str, Any]:
        """Merge static structure + dynamic records into flush-ready payload."""
        return {
            "request_id": self._request_id,
            "user_id": self._user_id,
            "session_id": self._session_id,
            "workflow_name": self._workflow_name,
            "tags": self._tags,
            "graph_structure": [asdict(n) for n in self._graph_structure],
            "records": [asdict(r) for r in self._records],
        }
```

### FlushWorker (hush-core/hush/core/tracing/flush_worker.py)

Global singleton. Non-blocking.

```python
class FlushWorker:
    def __init__(self, max_workers: int = 4):
        self._pool = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, tracers: List[BaseTracer], trace_data: Dict[str, Any]):
        for tracer in tracers:
            self._pool.submit(self._safe_flush, tracer, trace_data)

    @staticmethod
    def _safe_flush(tracer, trace_data):
        try:
            tracer.flush(trace_data)
        except Exception as e:
            logger.error("Flush to %s failed: %s", type(tracer).__name__, e)

    def shutdown(self):
        self._pool.shutdown(wait=True)

# Global instance
_worker: Optional[FlushWorker] = None

def get_flush_worker() -> FlushWorker:
    global _worker
    if _worker is None:
        _worker = FlushWorker()
        atexit.register(_worker.shutdown)
    return _worker
```

### BaseTracer (hush-core/hush/core/tracing/base.py)

Minimal interface. Each backend implements `flush()`.

```python
class BaseTracer:
    def __init__(self, tags: Optional[List[str]] = None):
        self._tags = tags or []

    def flush(self, trace_data: Dict[str, Any]) -> None:
        """Send trace data to backend. Called in thread pool."""
        raise NotImplementedError
```

### engine.run() (new flow)

```python
async def run(self, inputs, *, tracers=None, user_id=None, session_id=None,
              request_id=None):
    user_id = user_id or str(uuid.uuid4())
    session_id = session_id or str(uuid.uuid4())
    request_id = request_id or str(uuid.uuid4())

    # Pure data flow — no tracing fields
    state = self._schema.create_state(
        inputs=inputs, user_id=user_id,
        session_id=session_id, request_id=request_id,
    )

    # Separate trace collector (only if tracers configured)
    collector = None
    if tracers:
        collector = TraceCollector(self.graph, request_id, user_id, session_id)
        # Merge static tags from all tracers
        for t in tracers:
            for tag in t._tags:
                collector.add_tag(tag)

    result = await self.graph.run(state, collector=collector)

    # Non-blocking flush to all backends in parallel
    if tracers and collector:
        trace_data = collector.build_trace_data()
        get_flush_worker().submit(tracers, trace_data)

    result["$state"] = state
    return result
```

### Op.run() (new flow)

```python
async def run(self, state, collector=None):
    start_time = datetime.now()

    # ... execute op logic, write to state ...

    end_time = datetime.now()
    duration_ms = (end_time - start_time).total_seconds() * 1000

    if collector is not None:
        collector.record(
            op_name=self.full_name,
            context_id=context_id,
            inputs={v: state.get(self.full_name, v, ctx) for v in self.inputs},
            outputs={v: state.get(self.full_name, v, ctx) for v in self.outputs},
            start_time=start_time,
            end_time=end_time,
            duration_ms=duration_ms,
        )
```

LLMOp additionally passes `model`, `usage`, `cost`.

---

## hush-eyes API Design

### Ingest

```
POST /api/ingest
Content-Type: application/json

{
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "user_id": "user-123",
    "session_id": "session-456",
    "workflow_name": "rag_pipeline",
    "tags": ["prod", "gpt-4o"],
    "graph_structure": [
        {
            "op_name": "rag_pipeline",
            "op_type": "graph",
            "parent_name": null,
            "contain_generation": false
        },
        {
            "op_name": "rag_pipeline.embed",
            "op_type": "embedding",
            "parent_name": "rag_pipeline",
            "contain_generation": false
        },
        {
            "op_name": "rag_pipeline.llm",
            "op_type": "llm",
            "parent_name": "rag_pipeline",
            "contain_generation": true
        }
    ],
    "records": [
        {
            "op_name": "rag_pipeline",
            "context_id": null,
            "inputs": {"query": "What is Hush?"},
            "outputs": {"answer": "Hush is a workflow engine."},
            "start_time": "2025-01-15T10:30:00.000Z",
            "end_time": "2025-01-15T10:30:02.500Z",
            "duration_ms": 2500.0
        },
        {
            "op_name": "rag_pipeline.embed",
            "context_id": null,
            "inputs": {"texts": ["What is Hush?"]},
            "outputs": {"embeddings": "[[0.1, 0.2, ...]]"},
            "start_time": "2025-01-15T10:30:00.100Z",
            "end_time": "2025-01-15T10:30:00.300Z",
            "duration_ms": 200.0,
            "model": "bge-m3"
        },
        {
            "op_name": "rag_pipeline.llm",
            "context_id": null,
            "inputs": {"messages": [{"role": "user", "content": "..."}]},
            "outputs": {"content": "Hush is a workflow engine."},
            "start_time": "2025-01-15T10:30:00.300Z",
            "end_time": "2025-01-15T10:30:02.400Z",
            "duration_ms": 2100.0,
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 150, "completion_tokens": 80, "total_tokens": 230},
            "cost": 0.0042
        }
    ]
}

Response: 200 OK
{ "status": "ok", "trace_count": 3 }
```

### Query

```
GET /api/traces?page=1&limit=50&time_filter=86400&search=rag
→ {
    "traces": [
        {
            "request_id": "...",
            "workflow_name": "rag_pipeline",
            "tags": ["prod"],
            "start_time": "...",
            "duration_ms": 2500.0,
            "total_tokens": 230,
            "total_cost": 0.0042,
            "input_preview": "What is Hush?",
            "output_preview": "Hush is a workflow engine."
        }
    ],
    "total": 142,
    "page": 1,
    "limit": 50
  }

GET /api/traces/:request_id
→ {
    "request_id": "...",
    "workflow_name": "rag_pipeline",
    "user_id": "...",
    "session_id": "...",
    "tags": ["prod"],
    "nodes": [
        {
            "op_name": "rag_pipeline",
            "op_type": "graph",
            "parent_name": null,
            "context_id": null,
            "inputs": {...},
            "outputs": {...},
            "start_time": "...",
            "end_time": "...",
            "duration_ms": 2500.0,
            "model": null,
            "usage": null,
            "cost": null,
            "contain_generation": false
        },
        ...
    ]
  }

GET /api/db-info
→ { "path": "/home/user/.hush/traces.db", "size_bytes": 12800, "trace_count": 142 }

DELETE /api/traces
→ { "status": "ok", "deleted": 142 }

DELETE /api/traces/:request_id
→ { "status": "ok" }
```

### Static UI

```
GET /                → serves index.html (trace dashboard)
GET /main.js         → serves main.js
GET /styles.css      → serves styles.css
```

Open `http://localhost:8420` in any browser.

---

## webview/main.js Migration (postMessage → fetch)

Current VS Code message protocol → direct HTTP calls:

```javascript
// BEFORE (VS Code extension)
vscode.postMessage({ type: 'getTraceList', timeFilter: 86400, page: 1 });
window.addEventListener('message', e => {
    if (e.data.type === 'traceList') { renderTraceList(e.data.traces); }
});

// AFTER (standalone web app)
async function loadTraceList(timeFilter, page) {
    const res = await fetch(`/api/traces?time_filter=${timeFilter}&page=${page}`);
    const data = await res.json();
    renderTraceList(data.traces);
}

// BEFORE
vscode.postMessage({ type: 'getTraceDetail', requestId });
// AFTER
async function loadTraceDetail(requestId) {
    const res = await fetch(`/api/traces/${requestId}`);
    const data = await res.json();
    renderTraceDetail(data.nodes);
}

// BEFORE
vscode.postMessage({ type: 'getDbInfo' });
// AFTER
async function loadDbInfo() {
    const res = await fetch('/api/db-info');
    const data = await res.json();
    renderDbInfo(data);
}

// BEFORE
vscode.postMessage({ type: 'clearTraces' });
// AFTER
async function clearTraces() {
    await fetch('/api/traces', { method: 'DELETE' });
    loadTraceList();
}
```

The rendering functions (renderTraceList, renderTraceDetail, tree/timeline/graph views) stay unchanged. Only the data fetching layer changes.

---

## MemoryState Cleanup

Remove from MemoryState:

```python
# DELETE these slots
"_trace_metadata",
"_execution_order",
"_trace_store",
"_execution_count",
"_tags",

# DELETE these methods
record_trace()
record_execution()
has_trace_store (property)
trace_metadata (property)
execution_order (property)
tags (property)
add_tag()

# DELETE from __init__
trace_store parameter
all tracing initialization logic
```

MemoryState becomes purely: `schema` + `_cells` + `_user_id` + `_session_id` + `_request_id`.

---

## What Gets Deleted

### hush-core

| File | Lines (approx) | Reason |
|------|----------------|--------|
| `background/__init__.py` | ~20 | Package removed |
| `background/process.py` | ~450 | BackgroundProcess, drain thread, IPC |
| `background/worker.py` | ~280 | worker_loop, subprocess entry |
| `background/db.py` | ~500 | SQLite buffer operations |
| `background/flush.py` | ~140 | rebuild_flush_data, dispatch_flush |
| `tracers/store.py` | ~180 | TraceStore (dead-code wrapper) |
| `tracers/local.py` | ~80 | LocalTracer (replaced by hush-eyes) |
| **Subtotal** | **~1650** | |

### hush-eyes (current VS Code extension)

| File | Reason |
|------|--------|
| `src/extension.ts` | VS Code entry point |
| `src/tracePanel.ts` | VS Code WebviewPanel wrapper |
| `src/traceViewProvider.ts` | VS Code sidebar wrapper |
| `src/database.ts` | sql.js WASM SQLite reader |
| `tsconfig.json` | TypeScript config |
| `package.json` | Node.js manifest |
| `package-lock.json` | Node.js lockfile |
| `install.sh` / `install.ps1` | VS Code extension installers |
| `.vscodeignore` | VS Code packaging |

**Kept and adapted:** `webview/index.html`, `webview/main.js`, `webview/styles.css` → move to `src/static/`

---

## What Gets Added

### hush-core

| File | Lines (approx) | Purpose |
|------|----------------|---------|
| `tracing/__init__.py` | ~10 | Package exports |
| `tracing/collector.py` | ~100 | TraceCollector |
| `tracing/models.py` | ~40 | TraceRecord, NodeStructure dataclasses |
| `tracing/flush_worker.py` | ~50 | FlushWorker (ThreadPoolExecutor) |
| `tracing/base.py` | ~20 | BaseTracer (simplified) |
| **Subtotal** | **~220** | |

### hush-eyes (Rust server)

| File | Lines (approx) | Purpose |
|------|----------------|---------|
| `Cargo.toml` | ~25 | Rust dependencies |
| `src/main.rs` | ~40 | CLI + server startup |
| `src/config.rs` | ~30 | Config struct |
| `src/db/mod.rs` | ~20 | DB module |
| `src/db/schema.rs` | ~40 | CREATE TABLE |
| `src/db/write.rs` | ~60 | INSERT from ingest |
| `src/db/read.rs` | ~120 | SELECT for queries |
| `src/api/mod.rs` | ~30 | Router setup |
| `src/api/ingest.rs` | ~50 | POST /api/ingest handler |
| `src/api/query.rs` | ~100 | GET/DELETE handlers |
| `src/api/models.rs` | ~80 | serde structs |
| `src/static/*` | ~3800 | Adapted from webview (index.html + main.js + styles.css) |
| **Subtotal** | **~600 Rust + ~3800 adapted frontend** | |

### Net Python change: **-1650 + 220 = -1430 lines**

---

## Migration Steps

### Phase 1: Build hush-eyes Rust server

1. Initialize Cargo project, add dependencies
2. Implement `db/schema.rs` — CREATE TABLE traces
3. Implement `db/write.rs` — INSERT from ingest payload
4. Implement `db/read.rs` — SELECT queries (list, detail, db-info, delete)
5. Implement `api/ingest.rs` — POST /api/ingest
6. Implement `api/query.rs` — GET/DELETE endpoints
7. Adapt `webview/main.js` → `src/static/main.js` (postMessage → fetch)
8. Move `webview/index.html` and `webview/styles.css` → `src/static/`
9. Serve static files from Axum
10. Test: `cargo run -- serve --port 8420`, open browser

### Phase 2: Build hush-core tracing module

11. Create `tracing/models.py` — TraceRecord, NodeStructure
12. Create `tracing/collector.py` — TraceCollector with _extract_structure
13. Create `tracing/flush_worker.py` — FlushWorker singleton
14. Create `tracing/base.py` — simplified BaseTracer
15. Create `tracing/__init__.py` — exports
16. Add HushEyesTracer (thin HTTP client, POST to localhost:8420)

### Phase 3: Wire into engine and ops

17. Modify `engine.py` — create TraceCollector, pass to graph, flush after run
18. Modify `ops/base.py` — accept collector, call collector.record()
19. Modify `ops/graph/graph_op.py` — pass collector to children, call collector.record()
20. Modify `ops/iteration/base.py` — pass collector, call collector.record()
21. Modify `providers/ops/llm.py` — call collector.record() with model/usage/cost

### Phase 4: Simplify hush-telemetry

22. Simplify `tracers/langfuse.py` — flush receives structured trace_data
23. Simplify `tracers/otel.py` — same

### Phase 5: Cleanup

24. Delete `background/` package entirely
25. Delete `tracers/store.py`, `tracers/local.py`
26. Move `tracers/base.py` logic to `tracing/base.py`
27. Clean MemoryState — remove all tracing fields and methods
28. Delete old hush-eyes TypeScript/Node.js files
29. Update tests
30. Update architecture docs and CLAUDE.md files
