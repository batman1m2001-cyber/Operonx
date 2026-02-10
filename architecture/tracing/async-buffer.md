# AsyncTraceBuffer Design

## Overview

Background process xử lý trace writes và flushes non-blocking.

Location: `hush-core/hush/core/background/` (package)

## Architecture

```
Main Process                 Drain Thread          Background Process
     │                            │                       │
     │  enqueue(data)             │                       │
     ├──> deque.append()          │                       │
     │    (~0.1μs)                │                       │
     │                            ├── deque.popleft()     │
     │                            ├── queue.put() ───────>│
     │                            │                       ├── Insert to SQLite
     │                            │                       │
     │  enqueue(mark_complete)    │                       │
     ├──> deque.append()          │                       │
     │                            ├── queue.put() ───────>│
     │                            │                       ├── Update status
     │                            │                       │
     │                            │                       ├── Flush loop (periodic)
     │                            │                       │   └── For each pending:
     │                            │                       │       ├── Load traces from DB
     │                            │                       │       ├── Call tracer.flush()
     │                            │                       │       └── Update status
```

## Background Process

The `BackgroundProcess` class manages a separate process for trace writes and flushes.
It uses `multiprocessing.Process` by default, and falls back to `subprocess.Popen`
when running inside daemon workers (Gunicorn/Uvicorn) where multiprocessing is forbidden.

The process is started **lazily** on first use (when a tracer is provided), not at engine init.

```python
class BackgroundProcess:
    def _ensure_started(self):
        # Strategy 1: multiprocessing.Process (preferred)
        try:
            self._start_multiprocessing(...)
            self._start_drain_thread()
        except AssertionError:
            pass  # Daemon worker — fall back

        # Strategy 2: subprocess.Popen (bypasses daemon restriction)
        try:
            self._start_subprocess(...)
            self._start_drain_thread()
        except Exception:
            self._disabled = True  # Last resort

    def enqueue(self, data):
        """Near-zero latency (~0.1μs). Drain thread handles IPC."""
        self._buffer.append(data)  # collections.deque, thread-safe

    def write_trace(self, **kwargs):
        """Non-blocking enqueue via submit()."""
        self.submit(TaskType.TRACE_WRITE, kwargs)

    def mark_complete(self, **kwargs):
        """Non-blocking enqueue via submit()."""
        self.submit(TaskType.TRACE_COMPLETE, kwargs)
```

## Deque Buffer + Drain Thread

The hot path (main thread) never touches the IPC queue directly. Instead:

1. **Main thread**: `deque.append(task)` — ~0.1μs, lock-free
2. **Drain thread**: polls deque every 2ms, moves items to IPC queue
3. **Worker process**: reads from queue, writes to SQLite

```python
def _drain_loop(self):
    while not stop.is_set():
        # Drain all available items
        while True:
            try:
                item = buffer.popleft()
            except IndexError:
                break
            queue.put(item)

        if no items drained:
            stop.wait(0.002)  # 2ms sleep

    # Final drain on shutdown
    while buffer:
        queue.put(buffer.popleft())
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
    """Graceful shutdown - drain buffer, then stop process."""
    # 1. Stop drain thread (flushes remaining buffer items)
    self._drain_stop.set()
    self._drain_thread.join(timeout=2.0)

    # 2. Stop process
    if self._is_subprocess:
        self._process.stdin.close()  # EOF → SHUTDOWN
        self._process.wait(timeout)
    else:
        self._queue.put({"task_type": "shutdown", "data": {}})
        self._process.join(timeout)
```
