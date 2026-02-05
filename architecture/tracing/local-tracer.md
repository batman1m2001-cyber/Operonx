# SQLite Implementation Details

## Overview

Traces được lưu trong SQLite database qua background process để không block workflow execution.

Location: `hush-core/hush/core/tracers/store.py`

## TraceStore

```python
class TraceStore:
    __slots__ = ['_db_path']

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DEFAULT_DB_PATH
```

### Default Path

```python
DEFAULT_DB_PATH = Path(os.getenv("HUSH_TRACES_DB", "~/.hush/traces.db")).expanduser()
```

## Non-blocking Writes

Tất cả writes được gửi đến background process:

```python
def insert_node_trace(self, ...):
    """Non-blocking write."""
    bg = get_background(self._db_path)
    bg.write_trace(...)
```

## insert_node_trace()

```python
def insert_node_trace(
    self,
    request_id: str,
    workflow_name: str,
    node_name: str,
    parent_name: Optional[str],
    context_id: Optional[str],
    execution_order: int,
    start_time: Optional[str],       # ISO format
    end_time: Optional[str],         # ISO format
    duration_ms: Optional[float],
    input_data: Optional[Dict],
    output_data: Optional[Dict],
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    model: Optional[str] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    cost_usd: Optional[float] = None,
    contain_generation: bool = False,
    metadata: Optional[Dict] = None,
):
    bg = get_background(self._db_path)
    bg.write_trace(...)
```

## mark_request_complete()

```python
def mark_request_complete(
    self,
    request_id: str,
    tracer_type: str,
    tracer_config: Dict[str, Any],
    tags: Optional[List[str]] = None,
):
    """Mark request ready for flushing."""
    bg = get_background(self._db_path)
    bg.mark_complete(
        request_id=request_id,
        tracer_type=tracer_type,
        tracer_config=tracer_config,
        tags=tags,
    )
```

## Global Store

```python
_store: Optional[TraceStore] = None

def get_store(db_path: Optional[Path] = None) -> TraceStore:
    global _store
    if _store is None:
        _store = TraceStore(db_path)
    return _store
```

## Benefits

1. **Zero latency impact** - Writes don't block workflow
2. **Lower memory** - Traces not kept in MemoryState
3. **Crash resilience** - Durable storage
4. **Real-time observability** - Traces available immediately
