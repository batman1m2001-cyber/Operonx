"""BackgroundProcess manager with subprocess fallback.

This module provides the BackgroundProcess class that manages the background
worker. It tries multiprocessing.Process first, and falls back to
subprocess.Popen when running inside daemon workers (Gunicorn/Uvicorn).
"""

import atexit
import collections
import multiprocessing
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from hush.core.background.db import DEFAULT_DB_PATH
from hush.core.loggings import LOGGER


class TaskType(str, Enum):
    """Types of background tasks."""

    TRACE_WRITE = "trace_write"
    TRACE_COMPLETE = "trace_complete"
    TRACE_FLUSH = "trace_flush"
    SHUTDOWN = "shutdown"


@dataclass
class BackgroundTask:
    """A task to be executed in the background process."""

    task_type: TaskType
    data: Dict[str, Any]


class _PipeQueue:
    """Drop-in replacement for multiprocessing.Queue using stdin pipe.

    Writes JSON lines to the subprocess's stdin. Thread-safe via lock.
    """

    __slots__ = ["_pipe", "_lock"]

    def __init__(self, pipe):
        self._pipe = pipe
        self._lock = threading.Lock()

    def put(self, data: Dict[str, Any]) -> None:
        """Write a task as a JSON line to the pipe."""
        import orjson

        line = orjson.dumps(data) + b"\n"
        with self._lock:
            self._pipe.write(line)
            self._pipe.flush()


class BackgroundProcess:
    """Manages the unified background worker process.

    This class provides a simple interface for submitting tasks to the
    background process. The process is started lazily on first use.

    Spawn strategy:
        1. Try multiprocessing.Process (preferred, uses pickle + Queue)
        2. If blocked by daemon restriction, fall back to subprocess.Popen
           (uses JSON lines over stdin pipe)
        3. If both fail, disable tracing (last resort)

    Thread-safe: Multiple threads can submit tasks concurrently.
    """

    __slots__ = [
        "_db_path",
        "_process",
        "_queue",
        "_lock",
        "_started",
        "_owner_pid",
        "_disabled",
        "_is_subprocess",
        "_buffer",
        "_drain_thread",
        "_drain_stop",
    ]

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._process = None
        self._queue = None
        self._lock = threading.Lock()
        self._started = False
        self._owner_pid: Optional[int] = None
        self._disabled = False
        self._is_subprocess = False
        self._buffer: collections.deque = collections.deque()
        self._drain_thread: Optional[threading.Thread] = None
        self._drain_stop = threading.Event()

    def _ensure_started(self) -> None:
        """Ensure the background process is running."""
        if self._disabled:
            return

        # Detect fork: if we're in a different process than the one that
        # spawned the background worker, the old _process handle is invalid.
        if self._started and self._owner_pid != os.getpid():
            self._process = None
            self._queue = None
            self._started = False
            self._is_subprocess = False
            self._lock = threading.Lock()
            self._buffer = collections.deque()
            self._drain_thread = None
            self._drain_stop = threading.Event()

        if self._started and self._is_process_alive():
            return

        with self._lock:
            if self._disabled:
                return
            if self._started and self._is_process_alive():
                return

            # Get config path from current hub's storage
            config_path = None
            try:
                from hush.core.registry import get_hub

                hub = get_hub()
                if hasattr(hub._storage, "_file_path"):
                    config_path = str(hub._storage._file_path.resolve())
            except Exception:
                pass

            # Find .env file
            dotenv_path = None
            if config_path:
                env_file = Path(config_path).parent / ".env"
                if env_file.exists():
                    dotenv_path = str(env_file.resolve())
            if not dotenv_path:
                cwd_env = Path.cwd() / ".env"
                if cwd_env.exists():
                    dotenv_path = str(cwd_env.resolve())

            # Strategy 1: multiprocessing.Process (preferred)
            try:
                self._start_multiprocessing(config_path, dotenv_path)
                self._is_subprocess = False
                self._started = True
                self._owner_pid = os.getpid()
                self._start_drain_thread()
                return
            except AssertionError:
                LOGGER.info(
                    "Cannot use multiprocessing.Process (daemon worker). "
                    "Falling back to subprocess.Popen."
                )

            # Strategy 2: subprocess.Popen (bypasses daemon restriction)
            try:
                self._start_subprocess(config_path, dotenv_path)
                self._is_subprocess = True
                self._started = True
                self._owner_pid = os.getpid()
                self._start_drain_thread()
                return
            except Exception as e:
                self._disabled = True
                self._process = None
                self._queue = None
                LOGGER.warning(
                    "Cannot spawn background process (multiprocessing and subprocess both failed: %s). "
                    "Background trace writing is disabled for this worker.",
                    e,
                )

    def _start_multiprocessing(
        self, config_path: Optional[str], dotenv_path: Optional[str]
    ) -> None:
        """Start background worker via multiprocessing.Process."""
        from hush.core.background.worker import _background_worker

        ctx = multiprocessing.get_context("spawn")
        self._queue = ctx.Queue()
        self._process = ctx.Process(
            target=_background_worker,
            args=(self._queue, str(self._db_path), config_path, dotenv_path),
            daemon=True,
        )
        self._process.start()

    def _start_subprocess(self, config_path: Optional[str], dotenv_path: Optional[str]) -> None:
        """Start background worker via subprocess.Popen (bypasses daemon restriction)."""
        cmd = [
            sys.executable,
            "-m",
            "hush.core.background.worker",
            "--db-path",
            str(self._db_path),
        ]
        if config_path:
            cmd.extend(["--config-path", config_path])
        if dotenv_path:
            cmd.extend(["--dotenv-path", dotenv_path])

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=None,
            stderr=None,
        )
        self._queue = _PipeQueue(self._process.stdin)

    def enqueue(self, data: Dict[str, Any]) -> None:
        """Append a task to the buffer (near-zero latency).

        The drain thread will pick it up and send to the worker process.
        This is the preferred way to submit tasks from the hot path.
        """
        self._buffer.append(data)

    def _start_drain_thread(self) -> None:
        """Start the drain thread that moves items from buffer to queue."""
        self._drain_stop.clear()
        self._drain_thread = threading.Thread(
            target=self._drain_loop, daemon=True, name="hush-drain"
        )
        self._drain_thread.start()

    def _drain_loop(self) -> None:
        """Drain thread: move items from deque buffer to the IPC queue.

        Runs continuously, draining all available items each cycle.
        Uses a short sleep to avoid busy-waiting while keeping latency low.
        """
        buffer = self._buffer
        stop = self._drain_stop
        queue = self._queue

        while not stop.is_set():
            # Drain all available items
            drained = 0
            while True:
                try:
                    item = buffer.popleft()
                except IndexError:
                    break
                try:
                    queue.put(item)
                    drained += 1
                except Exception:
                    break

            if drained == 0:
                # No items — sleep briefly before checking again
                stop.wait(0.002)  # 2ms

        # Final drain on shutdown
        while True:
            try:
                item = buffer.popleft()
            except IndexError:
                break
            try:
                queue.put(item)
            except Exception:
                break

    def _is_process_alive(self) -> bool:
        """Check if the background process is still running."""
        if self._process is None:
            return False
        if self._is_subprocess:
            return self._process.poll() is None
        else:
            return self._process.is_alive()

    def submit(self, task_type: TaskType, data: Dict[str, Any]) -> None:
        """Submit a task to the background process."""
        self._ensure_started()
        if self._disabled:
            return
        self._queue.put({"task_type": task_type.value, "data": data})

    def write_trace(
        self,
        request_id: str,
        workflow_name: str,
        op_name: str,
        op_type: Optional[str] = None,
        parent_name: Optional[str] = None,
        context_id: Optional[str] = None,
        execution_order: int = 0,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        duration_ms: Optional[float] = None,
        input_data: Optional[Dict[str, Any]] = None,
        output_data: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        cost_usd: Optional[float] = None,
        contain_generation: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write a trace to the background process (non-blocking)."""
        self.submit(
            TaskType.TRACE_WRITE,
            {
                "request_id": request_id,
                "workflow_name": workflow_name,
                "op_name": op_name,
                "op_type": op_type,
                "parent_name": parent_name,
                "context_id": context_id,
                "execution_order": execution_order,
                "start_time": start_time,
                "end_time": end_time,
                "duration_ms": duration_ms,
                "input_data": input_data,
                "output_data": output_data,
                "user_id": user_id,
                "session_id": session_id,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cost_usd": cost_usd,
                "contain_generation": contain_generation,
                "metadata": metadata,
            },
        )

    def mark_complete(
        self,
        request_id: str,
        tracer_type: str,
        tracer_config: Dict[str, Any],
        tags: Optional[List[str]] = None,
    ) -> None:
        """Mark traces as ready for flushing (non-blocking).

        Uses enqueue() (buffer) instead of submit() (direct queue) to ensure
        ordering: trace_write items in the buffer are drained before this
        mark_complete reaches the worker.
        """
        self._ensure_started()
        if self._disabled:
            return
        self.enqueue(
            {
                "task_type": TaskType.TRACE_COMPLETE.value,
                "data": {
                    "request_id": request_id,
                    "tracer_type": tracer_type,
                    "tracer_config": tracer_config,
                    "tags": tags,
                },
            }
        )

    def shutdown(self, timeout: float = 5.0) -> None:
        """Shutdown the background process gracefully."""
        # Stop drain thread first — flushes remaining buffer items
        if self._drain_thread is not None:
            self._drain_stop.set()
            self._drain_thread.join(timeout=2.0)
            self._drain_thread = None

        if not self._is_process_alive():
            return

        if self._is_subprocess:
            # Close stdin pipe — triggers EOF in stdin reader → SHUTDOWN
            try:
                self._process.stdin.close()
            except Exception:
                pass
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._process.terminate()
        else:
            # Send shutdown signal via queue
            try:
                self._queue.put({"task_type": TaskType.SHUTDOWN.value, "data": {}})
            except Exception:
                pass
            self._process.join(timeout=timeout)
            if self._process.is_alive():
                self._process.terminate()

        self._process = None
        self._queue = None
        self._started = False
        self._is_subprocess = False
        self._buffer = collections.deque()
        self._drain_stop = threading.Event()

    @property
    def is_running(self) -> bool:
        """Check if background process is running."""
        return self._is_process_alive()

    @property
    def db_path(self) -> Path:
        """Get database path."""
        return self._db_path


# Global background process instance
_background: Optional[BackgroundProcess] = None
_background_lock = threading.Lock()


def get_background(db_path: Optional[Path] = None) -> BackgroundProcess:
    """Get global BackgroundProcess instance."""
    global _background
    if _background is None:
        with _background_lock:
            if _background is None:
                _background = BackgroundProcess(db_path)
    return _background


def shutdown_background() -> None:
    """Shutdown the global background process."""
    global _background
    if _background is not None:
        _background.shutdown()
        _background = None


# Register shutdown handler
atexit.register(shutdown_background)
