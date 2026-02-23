# Tracing Data Model

## Tổng quan

Tracing data model gồm 3 dataclasses trong `models.py` và một dict format (`trace_data`) mà `TraceCollector.collect()` trả về và `Tracer.flush()` nhận. Thiết kế tách biệt **static data** (cấu trúc graph, cố định sau compile) và **dynamic data** (I/O, timing, per-execution).

Location: `hush-core/hush/core/tracing/models.py`

---

## Dataclasses

### NodeStructure — Static metadata

Captured once từ compiled graph. Mỗi op trong graph tree có đúng một `NodeStructure`. Dữ liệu đọc từ op `@properties`, không thay đổi giữa các lần chạy.

```python
@dataclass
class NodeStructure:
    """Static metadata — captured once from compiled graph."""

    op_name: str                    # Full qualified name, e.g. "workflow.step1"
    op_type: str                    # Op type literal, e.g. "graph", "llm", "func"
    parent_name: Optional[str]      # Parent op's full_name, None for root
    contain_generation: bool        # True if op produces LLM generation data
```

**Ví dụ:**

```python
NodeStructure(
    op_name="rag_pipeline",
    op_type="graph",
    parent_name=None,           # Root op
    contain_generation=False,
)
NodeStructure(
    op_name="rag_pipeline.llm",
    op_type="llm",
    parent_name="rag_pipeline",
    contain_generation=True,    # LLM op → has model, usage, cost
)
```

**Nguồn dữ liệu:**

| Field | Đọc từ | Ghi chú |
|-------|--------|---------|
| `op_name` | `op.full_name` | Dot-separated path trong graph tree |
| `op_type` | `getattr(op, "type", "default")` | Literal type trên class |
| `parent_name` | `op.parent.full_name` | `None` nếu là root GraphOp |
| `contain_generation` | `op.contain_generation` | `True` cho LLMOp, ChainOp, v.v. |

### TraceRecord — Dynamic execution data

Mỗi op execution tạo một `TraceRecord`. Trong trường hợp loops, cùng một op có thể tạo nhiều records (mỗi iteration một record). Tất cả giá trị đọc từ `MemoryState` cells.

```python
@dataclass
class TraceRecord:
    """Dynamic data — read from state after execution."""

    op_name: str                              # Full qualified name
    context_id: Optional[str]                 # None or "[0]", "[1]", "[0].[1]"
    inputs: Dict[str, Any]                    # Op input values
    outputs: Dict[str, Any]                   # Op output values
    start_time: Optional[str] = None          # ISO 8601 format
    end_time: Optional[str] = None            # ISO 8601 format
    duration_ms: Optional[float] = None       # Execution time in milliseconds
    model: Optional[str] = None               # LLM model name (e.g. "gpt-4o")
    usage: Optional[Dict[str, Any]] = None    # Token usage dict
    cost: Optional[float] = None              # Cost in USD
    metadata: Optional[Dict[str, Any]] = None # Extra metadata
```

**Ví dụ — op thường:**

```python
TraceRecord(
    op_name="workflow.format_prompt",
    context_id=None,
    inputs={"template": "Answer: {question}", "question": "What is Hush?"},
    outputs={"prompt": "Answer: What is Hush?"},
    start_time="2025-01-15T10:30:00.100000",
    end_time="2025-01-15T10:30:00.102000",
    duration_ms=2.0,
    model=None,
    usage=None,
    cost=None,
)
```

**Ví dụ — LLM op:**

```python
TraceRecord(
    op_name="workflow.llm",
    context_id=None,
    inputs={"messages": [{"role": "user", "content": "What is Hush?"}]},
    outputs={"content": "Hush is a workflow engine.", "model_used": "gpt-4o",
             "tokens_used": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}},
    start_time="2025-01-15T10:30:00.102000",
    end_time="2025-01-15T10:30:01.500000",
    duration_ms=1398.0,
    model="gpt-4o",
    usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    cost=0.00042,
)
```

**Ví dụ — loop iteration:**

```python
# ForOp iterate 3 items → 3 records cho cùng một op_name
TraceRecord(op_name="workflow.loop.step", context_id="[0]", ...)
TraceRecord(op_name="workflow.loop.step", context_id="[1]", ...)
TraceRecord(op_name="workflow.loop.step", context_id="[2]", ...)
```

**Nguồn dữ liệu:**

| Field | Đọc từ | Ghi chú |
|-------|--------|---------|
| `op_name` | `op_map` iteration (key = `op.full_name`) | Derived from compiled graph, not stored in state |
| `context_id` | `state.iter_executed(op_name)` yields `(ctx, start_time)` | `None` cho non-loop ops, `"[0]"`, `"[1]"` for loops |
| `inputs` | `state[op_name, var, ctx]` cho mỗi var trong `op.inputs` | |
| `outputs` | `state[op_name, var, ctx]` cho mỗi var trong `op.outputs` | |
| `start_time` | `state[op_name, "start_time", ctx].isoformat()` | Convert `datetime` → ISO string |
| `end_time` | `state[op_name, "end_time", ctx].isoformat()` | |
| `duration_ms` | `state[op_name, "duration_ms", ctx]` | Float, milliseconds |
| `model` | `outputs["model_used"]` | Chỉ khi `op.contain_generation == True` |
| `usage` | `outputs["tokens_used"]` | Chỉ khi `op.contain_generation == True` |
| `cost` | `state[op_name, "cost_usd", ctx]` | Đọc trực tiếp từ state (không qua outputs) |

### TracePayload — Complete payload

Kết hợp static graph structure với dynamic execution records. Match format `IngestRequest` của hush-eyes server.

```python
@dataclass
class TracePayload:
    """Complete trace data for a single workflow execution."""

    request_id: str
    workflow_name: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    graph_structure: List[NodeStructure] = field(default_factory=list)
    records: List[TraceRecord] = field(default_factory=list)
```

> **Lưu ý:** `TracePayload` là dataclass "documentation-level" — trong thực tế, `TraceCollector.collect()` trả về `dict` (đã `asdict()`) thay vì instance `TracePayload`. Dataclass này tồn tại để document cấu trúc expected.

---

## trace_data Dict Format

Đây là dict mà `TraceCollector.collect()` trả về, `FlushWorker` truyền cho `tracer.flush()`. Format này match `IngestRequest` của hush-eyes server.

```python
trace_data = {
    # Metadata — from state
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "workflow_name": "rag_pipeline",
    "user_id": "user-123",                    # Optional
    "session_id": "session-456",              # Optional

    # Tags — merged static + dynamic by FlushWorker (per tracer)
    "tags": ["prod", "ml-team", "cache-hit"],

    # Static: graph structure — one entry per op in the graph tree
    "graph_structure": [
        {
            "op_name": "rag_pipeline",
            "op_type": "graph",
            "parent_name": None,
            "contain_generation": False,
        },
        {
            "op_name": "rag_pipeline.embed",
            "op_type": "embedding",
            "parent_name": "rag_pipeline",
            "contain_generation": False,
        },
        {
            "op_name": "rag_pipeline.llm",
            "op_type": "llm",
            "parent_name": "rag_pipeline",
            "contain_generation": True,
        },
    ],

    # Dynamic: execution records — one entry per op execution
    # Loops produce multiple records for the same op_name (different context_id)
    "records": [
        {
            "op_name": "rag_pipeline",
            "context_id": None,
            "inputs": {"query": "What is Hush?"},
            "outputs": {"answer": "Hush is a workflow engine."},
            "start_time": "2025-01-15T10:30:00.000000",
            "end_time": "2025-01-15T10:30:02.500000",
            "duration_ms": 2500.0,
            "model": None,
            "usage": None,
            "cost": None,
            "metadata": None,
        },
        {
            "op_name": "rag_pipeline.embed",
            "context_id": None,
            "inputs": {"texts": ["What is Hush?"]},
            "outputs": {"embeddings": [[0.1, 0.2]]},
            "start_time": "2025-01-15T10:30:00.100000",
            "end_time": "2025-01-15T10:30:00.300000",
            "duration_ms": 200.0,
            "model": "bge-m3",
            "usage": None,
            "cost": None,
            "metadata": None,
        },
        {
            "op_name": "rag_pipeline.llm",
            "context_id": None,
            "inputs": {"messages": [{"role": "user", "content": "..."}]},
            "outputs": {"content": "Hush is a workflow engine.", "model_used": "gpt-4o",
                        "tokens_used": {"prompt_tokens": 150, "completion_tokens": 80}},
            "start_time": "2025-01-15T10:30:00.300000",
            "end_time": "2025-01-15T10:30:02.400000",
            "duration_ms": 2100.0,
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 150, "completion_tokens": 80, "total_tokens": 230},
            "cost": 0.0042,
            "metadata": None,
        },
    ],
}
```

### graph_structure vs records

| Thuộc tính | graph_structure | records |
|-----------|----------------|---------|
| Tính chất | Static | Dynamic |
| Nguồn | Compiled graph (op @properties) | MemoryState cells |
| Số lượng | 1 per op trong graph | 1 per op execution (loops → N) |
| Khi nào thay đổi | Chỉ khi graph structure thay đổi | Mỗi lần chạy |
| Chứa gì | Type, parent, generation flag | I/O, timing, model, usage, cost |

---

## Context IDs

Context IDs xác định iteration cụ thể trong loop ops (ForOp, MapOp, WhileOp, AsyncIterOp).

### Format

| Loại | Context ID | Ý nghĩa |
|------|-----------|---------|
| Non-loop op | `None` | Chạy 1 lần, không có context |
| Simple loop, iteration 0 | `"[0]"` | Iteration đầu tiên |
| Simple loop, iteration 1 | `"[1]"` | Iteration thứ hai |
| Simple loop, iteration N | `"[N]"` | Iteration thứ N |
| Nested loop, outer 0 inner 1 | `"[0].[1]"` | Outer loop iteration 0, inner loop iteration 1 |
| Triple nested | `"[0].[1].[2]"` | 3 cấp loop lồng nhau |

### Ví dụ: Simple ForOp

```python
with ForOp.of(item=Each(["a", "b", "c"])) as loop:
    step = process(item=PARENT["item"])
    START >> step >> END
```

Records sinh ra:

```python
# Loop container
{"op_name": "workflow.loop", "context_id": None, ...}

# 3 iterations
{"op_name": "workflow.loop.step", "context_id": "[0]", "inputs": {"item": "a"}, ...}
{"op_name": "workflow.loop.step", "context_id": "[1]", "inputs": {"item": "b"}, ...}
{"op_name": "workflow.loop.step", "context_id": "[2]", "inputs": {"item": "c"}, ...}
```

### Ví dụ: Nested loops

```python
with ForOp.of(batch=Each([batch1, batch2])) as outer:
    with ForOp.of(item=Each(PARENT["batch"])) as inner:
        step = process(item=PARENT["item"])
        START >> step >> END
    START >> inner >> END
```

Records sinh ra:

```python
{"op_name": "workflow.outer", "context_id": None, ...}
{"op_name": "workflow.outer.inner", "context_id": "[0]", ...}    # batch1
{"op_name": "workflow.outer.inner.step", "context_id": "[0].[0]", ...}
{"op_name": "workflow.outer.inner.step", "context_id": "[0].[1]", ...}
{"op_name": "workflow.outer.inner", "context_id": "[1]", ...}    # batch2
{"op_name": "workflow.outer.inner.step", "context_id": "[1].[0]", ...}
{"op_name": "workflow.outer.inner.step", "context_id": "[1].[1]", ...}
```

### Parent lookup với context_id

Khi external tracers (Langfuse, OTEL) cần tìm parent span cho một child record, họ dùng context-aware lookup:

```python
# Record: op_name="loop.step", parent_name="loop", context_id="[0].[1]"

# Thử: "loop:[0].[1]" (parent cùng full context)
# Fallback: "loop:[0]" (parent ở outer context — strip last segment)

# Logic:
parent_key = parent_name
if context_id:
    # Try exact context match first
    context_parent_key = f"{parent_name}:{context_id}"
    if context_parent_key in known_spans:
        parent_key = context_parent_key
    else:
        # Strip last context segment
        last_dot = context_id.rfind(".")
        if last_dot > 0:
            parent_context = context_id[:last_dot]
            fallback_key = f"{parent_name}:{parent_context}"
            if fallback_key in known_spans:
                parent_key = fallback_key
```

---

## LLM-Specific Fields

Chỉ ops có `contain_generation == True` (LLMOp, ChainOp) mới có các fields `model`, `usage`, `cost` khác `None`.

### model

Đọc từ `outputs["model_used"]` — tên model thực tế mà provider trả về.

```python
model = outputs.get("model_used") if op.contain_generation else None
# e.g. "gpt-4o", "claude-3-sonnet", "bge-m3"
```

### usage

Đọc từ `outputs["tokens_used"]` — dict chứa token counts.

```python
usage = outputs.get("tokens_used") if op.contain_generation else None
# e.g. {"prompt_tokens": 150, "completion_tokens": 80, "total_tokens": 230}
```

Keys trong usage dict phụ thuộc vào provider, nhưng thường có:
- `prompt_tokens` (hoặc `input_tokens`)
- `completion_tokens` (hoặc `output_tokens`)
- `total_tokens`

### cost

Đọc trực tiếp từ state (không qua outputs):

```python
cost = state[op_name, "cost_usd", ctx]
# e.g. 0.0042 (USD)
```

Cost được op tự tính và ghi vào state cell `cost_usd`. Khác với `model` và `usage` nằm trong outputs, `cost` là state variable riêng.

---

## Serialization

`TraceCollector.collect()` dùng `dataclasses.asdict()` để convert `NodeStructure` và `TraceRecord` thành dicts trước khi trả về:

```python
return {
    ...
    "graph_structure": [asdict(n) for n in graph_structure],
    "records": [asdict(r) for r in records],
}
```

`HushEyesTracer.flush()` dùng `json.dumps(default=str)` để serialize datetime, UUID, v.v.:

```python
body = json.dumps(trace_data, default=str).encode("utf-8")
```

---

## Xem thêm

- [Overview](overview.md) — Kiến trúc tổng thể, design principles
- [External Backends](external-backends.md) — Cách LangfuseTracer và OTELTracer consume trace_data
