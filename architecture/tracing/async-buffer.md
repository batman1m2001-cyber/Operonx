# AsyncTraceBuffer Design

## Overview

Background process xử lý trace writes và flushes non-blocking.

Location: `hush-core/hush/core/background.py`

## Architecture

```
Main Process                Background Process
     │                            │
     │  write_trace(data)         │
     ├───────────────────────────>│
     │  (non-blocking)            │
     │                            ├── Insert to SQLite
     │                            │
     │  mark_complete(req_id)     │
     ├───────────────────────────>│
     │  (non-blocking)            │
     │                            ├── Update status
     │                            │
     │                            ├── Flush loop (periodic)
     │                            │   └── For each pending:
     │                            │       ├── Load traces from DB
     │                            │       ├── Call tracer.flush()
     │                            │       └── Update status
```

## Background Process

```python
class BackgroundWorker:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._queue = Queue()
        self._running = True
        self._thread = Thread(target=self._worker_loop)
        self._thread.start()

    def write_trace(self, **kwargs):
        """Non-blocking enqueue."""
        self._queue.put(("trace", kwargs))

    def mark_complete(self, **kwargs):
        """Non-blocking enqueue."""
        self._queue.put(("complete", kwargs))

    def _worker_loop(self):
        while self._running:
            try:
                msg_type, data = self._queue.get(timeout=1.0)
                if msg_type == "trace":
                    self._insert_trace(data)
                elif msg_type == "complete":
                    self._mark_complete(data)
            except Empty:
                self._flush_pending()
```

## Flush Logic

```python
def _flush_pending(self):
    """Flush all pending requests."""
    pending = self._get_pending_requests()

    for request in pending:
        try:
            # Mark as flushing
            self._update_status(request.id, "flushing")

            # Load traces
            traces = self._get_traces(request.id)

            # Get tracer class
            tracer_cls = get_registered_tracers().get(request.tracer_type)
            if not tracer_cls:
                raise ValueError(f"Unknown tracer: {request.tracer_type}")

            # Build flush data
            flush_data = {
                "request_id": request.id,
                "workflow_name": request.workflow_name,
                "traces": traces,
                "tags": request.tags,
                **request.tracer_config,
            }

            # Call flush
            tracer_cls.flush(flush_data)

            # Mark as flushed
            self._update_status(request.id, "flushed")

        except Exception as e:
            self._handle_failure(request, e)
```

## Retry Logic

```python
MAX_RETRIES = 3

def _handle_failure(self, request, error):
    request.retry_count += 1

    if request.retry_count >= MAX_RETRIES:
        self._update_status(request.id, "failed", error=str(error))
    else:
        # Back to pending for retry
        self._update_status(request.id, "pending")
```

## Global Access

```python
_background: Optional[BackgroundWorker] = None

def get_background(db_path: Path = None) -> BackgroundWorker:
    global _background
    if _background is None:
        _background = BackgroundWorker(db_path or DEFAULT_DB_PATH)
    return _background

def shutdown_background():
    global _background
    if _background:
        _background.shutdown()
        _background = None
```

## Graceful Shutdown

```python
def shutdown(self, timeout: float = 5.0):
    """Graceful shutdown - flush remaining items."""
    self._running = False

    # Process remaining queue items
    while not self._queue.empty():
        try:
            msg_type, data = self._queue.get_nowait()
            # Process...
        except Empty:
            break

    # Final flush
    self._flush_pending()

    self._thread.join(timeout)
```
