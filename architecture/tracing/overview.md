# Tracing System — Overview

## Tổng quan

Hệ thống tracing mới tách biệt hoàn toàn khỏi ops và state. Thay vì ghi traces vào SQLite qua background process + IPC như trước, hệ thống mới chỉ đọc dữ liệu từ state sau khi workflow hoàn thành, rồi gửi đến các tracers trong background thread pool.

Location: `hush-core/hush/core/tracing/`

## Nguyên tắc thiết kế

### 1. Ops không biết về tracing

Không có `collector.record()` hay `state.record_trace()` bên trong op logic. Ops chỉ ghi I/O, timing, cost vào state như bình thường — tracing đọc dữ liệu đó sau.

```python
# Op.run() chỉ lưu dữ liệu vào state — KHÔNG gọi bất kỳ tracing API nào
state[op_name, "start_time", ctx] = start_time
state[op_name, "end_time", ctx] = end_time
state[op_name, "duration_ms", ctx] = duration_ms
# Outputs (model_used, tokens_used, cost_usd...) cũng nằm trong state
```

### 2. Cả collect (CPU) và flush (I/O) chạy trong background thread pool

`TraceCollector.collect()` (CPU-bound, microseconds) và `tracer.flush()` (I/O-bound, HTTP calls) đều chạy trong `ThreadPoolExecutor`, không chặn main async thread.

### 3. Static data captured once, dynamic data read from state

- **Static**: `op_type`, `parent_name`, `contain_generation` — đọc từ op `@properties` trong compiled graph. Dữ liệu này cố định sau khi compile, không thay đổi giữa các lần chạy.
- **Dynamic**: `inputs`, `outputs`, `start_time`, `end_time`, `duration_ms`, `model`, `usage`, `cost` — đọc từ state cells sau khi execution hoàn thành.

### 4. Non-blocking: engine.run() trả về ngay lập tức

`FlushWorker.submit()` chỉ đẩy task vào thread pool và return. Caller không cần đợi traces được gửi xong.

---

## Kiến trúc tổng thể

```
Op.run()
  → stores I/O, timing, cost to state (no tracing awareness)

engine.run() completes
  → FlushWorker.submit(tracers, graph, state)          ← returns immediately
    → ThreadPoolExecutor thread:
      → TraceCollector.collect(graph, state)             ← CPU-bound, microseconds
        → _build_op_map(graph)                           ← walk graph tree recursively
        → _collect_graph_structure(op_map)                ← read op @properties (static)
        → _collect_records(op_map, state)                 ← iterate op_map + state.iter_executed() (dynamic)
      → for tracer in tracers:
        → _merge_tags(dynamic_tags, tracer.tags)          ← static + dynamic tags
        → tracer.flush(trace_data)                        ← I/O-bound (HTTP, SDK calls)
          ├→ HushEyesTracer   → POST http://localhost:8420/api/ingest
          ├→ LangfuseTracer   → Langfuse SDK
          └→ OTELTracer       → OpenTelemetry SDK
```

### Luồng dữ liệu chi tiết

```
┌─────────────────────────────────────────────────────────────────┐
│                        engine.run()                              │
│                                                                  │
│  1. compile graph → StateSchema                                  │
│  2. create MemoryState                                           │
│  3. execute ops → ops ghi I/O, timing, cost vào state           │
│  4. if tracers:                                                  │
│     get_flush_worker().submit(tracers, graph, state)             │
│  5. return result                 ← KHÔNG đợi flush             │
│                                                                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                   FlushWorker (background thread)                │
│                                                                  │
│  _safe_collect_and_flush(tracers, graph, state):                 │
│                                                                  │
│  ┌─ STEP 1: Collect ─────────────────────────────────────────┐  │
│  │  collector = TraceCollector()                              │  │
│  │  trace_data = collector.collect(graph, state)              │  │
│  │                                                            │  │
│  │  trace_data = {                                            │  │
│  │    request_id, workflow_name, user_id, session_id,         │  │
│  │    tags: [...],              ← dynamic only (from state)   │  │
│  │    graph_structure: [...],   ← static (from graph)         │  │
│  │    records: [...],           ← dynamic (from state)        │  │
│  │  }                                                         │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌─ STEP 2: Flush (per tracer) ──────────────────────────────┐  │
│  │  for tracer in tracers:                                    │  │
│  │    merged = _merge_tags(trace_data.tags, tracer.tags)      │  │
│  │    data = {**trace_data, "tags": merged}                   │  │
│  │    tracer.flush(data)                                      │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. Tracer — `base.py` (42 lines)

Base class cho tất cả tracer implementations. Chỉ có 2 thứ: static tags và `flush()`.

```python
class Tracer:
    def __init__(self, tags: Optional[List[str]] = None):
        self._tags = tags or []

    @property
    def tags(self) -> List[str]:
        return self._tags.copy()

    def flush(self, trace_data: Dict[str, Any]) -> None:
        """Called by FlushWorker in a background thread."""
        raise NotImplementedError
```

**So sánh với BaseTracer cũ:**

| Thuộc tính | BaseTracer (cũ) | Tracer (mới) |
|-----------|----------------|-------------|
| `flush()` | `@staticmethod`, chạy trong subprocess | Instance method, chạy trong thread pool |
| `_get_tracer_config()` | Required (serialize cho subprocess) | Không cần |
| `_merge_tags()` | Method trên tracer | Function độc lập trong `flush_worker.py` |
| `flush_in_background()` | Method phức tạp (SQLite + background process) | Không có (FlushWorker xử lý) |
| `shutdown_worker()` | Class method | Không có (atexit tự xử lý) |

### 2. TraceCollector — `collector.py` (123 lines)

Thu thập toàn bộ trace data từ graph (static) + state (dynamic) sau khi workflow chạy xong.

```python
class TraceCollector:
    def collect(self, graph, state) -> Dict[str, Any]:
        # 1. Build op lookup: full_name → op object
        op_map = {}
        self._build_op_map(graph, op_map)

        # 2. Static: graph structure from op @properties
        graph_structure = self._collect_graph_structure(op_map)

        # 3. Dynamic: iterate op_map, derive execution from start_time cells
        records = self._collect_records(op_map, state)

        # 4. Build payload
        return {
            "request_id": state.request_id,
            "workflow_name": graph.name,
            "user_id": state.user_id,
            "session_id": state.session_id,
            "tags": list(state.tags) if state.tags else [],
            "graph_structure": [asdict(n) for n in graph_structure],
            "records": [asdict(r) for r in records],
        }
```

#### `_build_op_map(op, result)` — Walk graph tree

Duyệt đệ quy từ root `GraphOp`, thu thập tất cả ops vào dict `{full_name: op}`:

```python
def _build_op_map(self, op, result):
    result[op.full_name] = op
    if hasattr(op, "_ops") and op._ops:
        for child in op._ops.values():
            self._build_op_map(child, result)
```

#### `_collect_graph_structure(op_map)` — Read op @properties (static)

Tạo `NodeStructure` cho mỗi op, đọc `type`, `parent`, `contain_generation` từ op properties:

```python
def _collect_graph_structure(self, op_map):
    return [
        NodeStructure(
            op_name=op.full_name,
            op_type=getattr(op, "type", "default"),
            parent_name=op.parent.full_name if op.parent else None,
            contain_generation=op.contain_generation,
        )
        for op in op_map.values()
    ]
```

#### `_collect_records(op_map, state)` — Iterate op_map + state.iter_executed() (dynamic)

Iterates all ops in `op_map` and calls `state.iter_executed(op_name)` for each one to discover which contexts executed (derived from `start_time` cells). Then reads I/O, timing, LLM fields from state cells:

```python
def _collect_records(self, op_map, state):
    records = []

    for op_name, op in op_map.items():
        for ctx, start_time in state.iter_executed(op_name):
            # Read I/O from state
            inputs = {v: state[op_name, v, ctx] for v in (op.inputs or {})}
            outputs = {v: state[op_name, v, ctx] for v in (op.outputs or {})}

            # Read timing from state
            end_time = state[op_name, "end_time", ctx]
            duration_ms = state[op_name, "duration_ms", ctx]

            # LLM-specific: model & usage are in outputs, cost in state
            model = outputs.get("model_used") if op.contain_generation else None
            usage = outputs.get("tokens_used") if op.contain_generation else None
            cost = state[op_name, "cost_usd", ctx]

            records.append(TraceRecord(...))
    return records
```

**Key change:** Execution history is no longer stored in a separate `_execution_order` list. Instead, `iter_executed()` derives it from `start_time` cells — if an op has a non-`None` `start_time` for a given context, it ran. This eliminates the coupling between ops and tracing (ops no longer need to call `state.record_execution()`).

### 3. FlushWorker — `flush_worker.py` (95 lines)

Thread pool singleton quản lý collect + flush trong background.

```python
class FlushWorker:
    def __init__(self, max_workers: int = 4):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="hush-trace"
        )

    def submit(self, tracers, graph, state):
        """Returns immediately. Background thread handles collect + flush."""
        self._executor.submit(self._safe_collect_and_flush, tracers, graph, state)

    def _safe_collect_and_flush(self, tracers, graph, state):
        try:
            collector = TraceCollector()
            trace_data = collector.collect(graph, state)

            for tracer in tracers:
                try:
                    merged = _merge_tags(trace_data.get("tags", []), tracer.tags)
                    data = {**trace_data, "tags": merged if merged else None}
                    tracer.flush(data)
                except Exception:
                    LOGGER.exception("Failed to flush to %s", type(tracer).__name__)
        except Exception:
            LOGGER.exception("Failed to collect trace data")

    def shutdown(self, wait=True):
        self._executor.shutdown(wait=wait)
```

#### Singleton pattern với atexit

```python
_worker: FlushWorker | None = None

def get_flush_worker() -> FlushWorker:
    global _worker
    if _worker is None:
        _worker = FlushWorker()
        atexit.register(_worker.shutdown)
    return _worker
```

- Lazy-init: thread pool chỉ tạo khi có tracer
- `atexit.register()`: đảm bảo flush hết pending tasks khi interpreter exit
- Singleton: toàn bộ process dùng chung một thread pool

### 4. HushEyesTracer — `hush_eyes.py` (69 lines)

Tracer built-in gửi traces đến hush-eyes local server. Dùng stdlib `urllib.request` (không cần thêm dependency).

```python
class HushEyesTracer(Tracer):
    def __init__(self, host="127.0.0.1", port=8420, tags=None):
        super().__init__(tags=tags)
        self._url = f"http://{host}:{port}/api/ingest"

    def flush(self, trace_data):
        body = json.dumps(trace_data, default=str).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    LOGGER.warning("hush-eyes returned status %d", resp.status)
        except Exception:
            LOGGER.debug("Could not reach hush-eyes at %s", self._url)
```

**Đặc điểm:**
- Dùng `urllib.request` (stdlib) thay vì `requests` — zero external dependency
- `json.dumps(default=str)` — serialize `datetime`, `UUID`, v.v. tự động
- Silently ignore connection errors — server có thể không chạy, không crash workflow
- Timeout 5 giây cho mỗi request

---

## Tag Merging

Tags đến từ 2 nguồn, merge tại flush time:

### Static tags (từ tracer constructor)

```python
tracer = HushEyesTracer(tags=["prod", "ml-team"])
# tracer.tags == ["prod", "ml-team"]
```

### Dynamic tags (từ op outputs via state._tags)

```python
@op
def process(data):
    if data["source"] == "cache":
        return {"result": data, "$tags": ["cache-hit"]}
    return {"result": process(data), "$tags": ["processed"]}
```

Key `$tags` trong output được engine extract ra `state._tags`.

### Merge logic

Static tags đặt trước, rồi append dynamic tags (deduplicated):

```python
def _merge_tags(dynamic_tags, static_tags):
    merged = list(static_tags)        # Static tags first
    for tag in dynamic_tags:
        if tag not in merged:         # Dedup
            merged.append(tag)
    return merged
```

Ví dụ:
- Static: `["prod", "ml-team"]`
- Dynamic: `["cache-hit", "prod"]` (`"prod"` trùng → bỏ qua)
- Merged: `["prod", "ml-team", "cache-hit"]`

**Merge xảy ra per-tracer** — mỗi tracer có thể có static tags khác nhau:

```python
result = await engine.run(
    inputs={...},
    tracer=[
        HushEyesTracer(tags=["dev"]),
        LangfuseTracer(resource="langfuse:default", tags=["prod"]),
    ],
)
# HushEyesTracer nhận: ["dev", ...dynamic...]
# LangfuseTracer nhận: ["prod", ...dynamic...]
```

---

## Engine Integration

```python
# hush-core/hush/core/engine.py (relevant lines)

async def run(self, inputs, *, tracer=None, ...):
    # ... compile, create state, execute workflow ...

    # Collect + flush in background thread (non-blocking)
    if tracer:
        from hush.core.tracing import get_flush_worker
        tracers = tracer if isinstance(tracer, list) else [tracer]
        get_flush_worker().submit(tracers, self.graph, state)

    return result
```

Engine chỉ cần 2 dòng code để tích hợp tracing. Không có tracing logic nào trong ops, state, hay engine core.

---

## So sánh với kiến trúc cũ

| Khía cạnh | Kiến trúc cũ | Kiến trúc mới |
|-----------|-------------|-------------|
| Ops awareness | `state.record_trace()` trong mỗi op | Ops không biết về tracing |
| Storage | SQLite via background process + IPC | Không có intermediate storage |
| Data flow | Op → deque → drain thread → IPC → subprocess → SQLite → rebuild → flush | State → TraceCollector.collect() → tracer.flush() |
| Background mechanism | Separate OS process (multiprocessing/subprocess) | ThreadPoolExecutor(4) |
| Static data | Recorded mỗi execution | Captured once từ compiled graph |
| Tag merge | Trong BaseTracer._merge_tags() | Trong FlushWorker._merge_tags() |
| External tracers | `@staticmethod flush()` (subprocess) | Instance method `flush()` (thread pool) |
| Code size | ~1650 lines (background/ + tracers/) | ~330 lines (tracing/) |

---

## Source Files

Tất cả nằm trong `hush-core/hush/core/tracing/`:

| File | Lines | Mục đích |
|------|-------|---------|
| `__init__.py` | 26 | Package exports |
| `base.py` | 42 | Tracer base class |
| `collector.py` | 123 | TraceCollector — đọc graph + state |
| `flush_worker.py` | 95 | FlushWorker — ThreadPoolExecutor singleton |
| `hush_eyes.py` | 69 | HushEyesTracer — HTTP POST to localhost:8420 |
| `models.py` | 57 | NodeStructure, TraceRecord, TracePayload dataclasses |
| **Total** | **412** | |

## Xem thêm

- [Data Model](data-model.md) — Chi tiết format `trace_data`, dataclasses, context IDs
- [External Backends](external-backends.md) — LangfuseTracer, OTELTracer trong hush-telemetry
- [Refactor Plan](refactor-plan.md) — Lịch sử migration từ kiến trúc cũ sang mới
