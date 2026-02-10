# AsyncTraceBuffer Design

## Overview

Background process xử lý trace writes và flushes non-blocking.

Location: `hush-core/hush/core/background/` (package)

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

The `BackgroundProcess` class manages a separate process for trace writes and flushes.
It uses `multiprocessing.Process` by default, and falls back to `subprocess.Popen`
when running inside daemon workers (Gunicorn/Uvicorn) where multiprocessing is forbidden.

```python
class BackgroundProcess:
    def _ensure_started(self):
        # Strategy 1: multiprocessing.Process (preferred)
        try:
            self._start_multiprocessing(...)
        except AssertionError:
            pass  # Daemon worker — fall back

        # Strategy 2: subprocess.Popen (bypasses daemon restriction)
        try:
            self._start_subprocess(...)
        except Exception:
            self._disabled = True  # Last resort

    def write_trace(self, **kwargs):
        """Non-blocking enqueue."""
        self._queue.put({"task_type": "trace_write", "data": kwargs})

    def mark_complete(self, **kwargs):
        """Non-blocking enqueue."""
        self._queue.put({"task_type": "mark_complete", "data": kwargs})
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
_background: Optional[BackgroundProcess] = None

def get_background(db_path: Path = None) -> BackgroundProcess:
    global _background
    if _background is None:
        _background = BackgroundProcess(db_path or DEFAULT_DB_PATH)
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
