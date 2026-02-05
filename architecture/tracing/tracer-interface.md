# BaseTracer Abstract Design

## Overview

`BaseTracer` là abstract class cho tất cả tracer implementations. Traces được ghi vào SQLite qua background process, sau đó flush đến external services.

Location: `hush-core/hush/core/tracers/base.py`

## Class Definition

```python
class BaseTracer(ABC):
    _tags: List[str]  # Static tags

    def __init__(self, tags: Optional[List[str]] = None):
        self._tags = tags or []

    @abstractmethod
    def _get_tracer_config(self) -> Dict[str, Any]:
        """Return tracer-specific config for serialization."""
        pass

    @staticmethod
    @abstractmethod
    def flush(flush_data: Dict[str, Any]) -> None:
        """Execute flush logic (called by background process)."""
        pass
```

## Key Methods

### flush_in_background()

```python
def flush_in_background(self, workflow_name: str, state: MemoryState) -> None:
    """Mark trace as complete and trigger background flushing."""
    merged_tags = self._merge_tags(state)

    if state.has_trace_store:
        # New mode: traces already written incrementally
        store = state._trace_store
        store.mark_request_complete(
            request_id=state.request_id,
            tracer_type=self.__class__.__name__,
            tracer_config=self._get_tracer_config(),
            tags=merged_tags,
        )
    else:
        # Legacy mode: batch insert
        self._insert_legacy_traces(store, workflow_name, state, merged_tags)
```

### _merge_tags()

```python
def _merge_tags(self, state: MemoryState) -> List[str]:
    """Merge static tracer tags with dynamic state tags."""
    merged = list(self._tags)
    for tag in state.tags:
        if tag not in merged:
            merged.append(tag)
    return merged
```

## Tags System

### Static Tags

Set at tracer initialization:

```python
tracer = LangfuseTracer(tags=["prod", "ml-team"])
```

### Dynamic Tags

Added during execution via `$tags`:

```python
@code_node
def process(data):
    if data["source"] == "cache":
        return {"result": data, "$tags": ["cache-hit"]}
    return {"result": process(data), "$tags": ["processed"]}
```

## Registering Tracers

```python
from hush.core.tracers import BaseTracer, register_tracer

@register_tracer
class MyTracer(BaseTracer):
    def __init__(self, api_key: str, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key

    def _get_tracer_config(self) -> Dict[str, Any]:
        return {"api_key": self.api_key}

    @staticmethod
    def flush(flush_data: Dict[str, Any]) -> None:
        # Send to your platform
        pass
```

## Tracer Registry

```python
_TRACER_REGISTRY: Dict[str, type] = {}

def register_tracer(tracer_cls: type) -> type:
    _TRACER_REGISTRY[tracer_cls.__name__] = tracer_cls
    return tracer_cls

def get_registered_tracers() -> Dict[str, type]:
    return _TRACER_REGISTRY.copy()
```

## Shutdown

```python
# Graceful shutdown
BaseTracer.shutdown_worker(timeout=5.0)
```
