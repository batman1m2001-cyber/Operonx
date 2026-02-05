"""Unified background process for non-blocking operations.

This module provides a single background process (BACKGROUND_PROCESS) that handles
all tasks that should not block the main workflow execution:
- Trace writing to SQLite
- Trace flushing to external services (Langfuse, etc.)
- Future: metrics collection, cleanup tasks, etc.

The background process is started lazily on first use and runs as a daemon.

Example:
    ```python
    from hush.core.background import get_background, BackgroundTask

    # Send a task to background
    bg = get_background()
    bg.submit(BackgroundTask(
        task_type="trace_write",
        data={...}
    ))

    # Or use convenience methods
    bg.write_trace(...)
    bg.mark_complete(...)
    ```
"""

import atexit
import json
import multiprocessing
import os
import signal
import sqlite3
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import time, sleep
from typing import Any, Dict, List, Optional, Tuple

# Default database path - can be overridden via HUSH_TRACES_DB env var
DEFAULT_DB_PATH = Path(os.environ.get("HUSH_TRACES_DB", Path.home() / ".hush" / "traces.db"))


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


def _init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize SQLite database with schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS traces (
            -- Identity
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            workflow_name TEXT NOT NULL,

            -- Node identity
            node_name TEXT,
            node_type TEXT,
            parent_name TEXT,
            context_id TEXT,
            execution_order INTEGER,

            -- Timing
            start_time TEXT,
            end_time TEXT,
            duration_ms REAL,

            -- LLM fields
            model TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            cost_usd REAL,

            -- Variable data (JSON)
            input TEXT,
            output TEXT,

            -- Metadata
            user_id TEXT,
            session_id TEXT,
            tracer_type TEXT,
            tracer_config TEXT,
            contain_generation INTEGER DEFAULT 0,
            metadata TEXT,

            -- Tags (JSON array of strings)
            tags TEXT,

            -- Status
            status TEXT DEFAULT 'pending',
            retry_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            flushed_at REAL,
            error TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_request ON traces(request_id);
        CREATE INDEX IF NOT EXISTS idx_status ON traces(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_model ON traces(model) WHERE model IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_cost ON traces(cost_usd) WHERE cost_usd IS NOT NULL;
    """)

    # Migration: Add columns if they don't exist (for databases created before these features)
    cursor = conn.execute("PRAGMA table_info(traces)")
    columns = {row[1] for row in cursor.fetchall()}
    if "tags" not in columns:
        conn.execute("ALTER TABLE traces ADD COLUMN tags TEXT")
    if "node_type" not in columns:
        conn.execute("ALTER TABLE traces ADD COLUMN node_type TEXT")

    conn.commit()
    return conn


def _write_trace(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    """Write a single trace to database."""
    now = time()

    # Calculate duration_ms from start_time/end_time if not provided
    duration_ms = data.get("duration_ms")
    if duration_ms is None and data.get("start_time") and data.get("end_time"):
        try:
            from datetime import datetime
            start = datetime.fromisoformat(data["start_time"])
            end = datetime.fromisoformat(data["end_time"])
            duration_ms = (end - start).total_seconds() * 1000
        except (ValueError, TypeError):
            pass

    conn.execute("""
        INSERT INTO traces (
            request_id, workflow_name, node_name, node_type, parent_name, context_id,
            execution_order, start_time, end_time, duration_ms,
            model, prompt_tokens, completion_tokens, total_tokens, cost_usd,
            input, output, user_id, session_id,
            contain_generation, metadata, status, retry_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'writing', 0, ?)
    """, (
        data["request_id"],
        data["workflow_name"],
        data["node_name"],
        data.get("node_type"),
        data.get("parent_name"),
        data.get("context_id"),
        data.get("execution_order", 0),
        data.get("start_time"),
        data.get("end_time"),
        duration_ms,
        data.get("model"),
        data.get("prompt_tokens"),
        data.get("completion_tokens"),
        data.get("total_tokens"),
        data.get("cost_usd"),
        json.dumps(data.get("input_data")) if data.get("input_data") else None,
        json.dumps(data.get("output_data")) if data.get("output_data") else None,
        data.get("user_id"),
        data.get("session_id"),
        1 if data.get("contain_generation") else 0,
        json.dumps(data.get("metadata")) if data.get("metadata") else None,
        now,
    ))
    conn.commit()


def _create_iteration_groups(conn: sqlite3.Connection, request_id: str) -> None:
    """Create synthetic iteration group traces to group children by context_id.

    Context_id is chained: [0], [1], [0].[0], [0].[1], [1].[0], etc.
    - [0] means first iteration of outer loop
    - [0].[1] means first outer iteration, second inner iteration

    We group nodes by (parent_name, context_id) and create iteration[N] nodes.

    Example:
        outer_loop.validate (ctx=[0]) -> outer_loop.iteration[0].validate
        outer_loop.inner_loop.scale (ctx=[0].[1]) -> outer_loop.inner_loop.iteration[1].scale
                                                     (under outer_loop.iteration[0].inner_loop)
    """
    cursor = conn.execute("""
        SELECT id, node_name, parent_name, context_id, start_time, end_time,
               duration_ms, workflow_name, user_id, session_id,
               prompt_tokens, completion_tokens, total_tokens, cost_usd
        FROM traces
        WHERE request_id = ? AND status = 'writing'
        ORDER BY execution_order
    """, (request_id,))
    rows = cursor.fetchall()

    if not rows:
        return

    # Build a lookup of parent loop nodes (with input/output/metadata)
    # to slice per-iteration values
    parent_loop_cache: Dict[str, Dict] = {}

    def _get_parent_loop(parent_name: str) -> Optional[Dict]:
        if parent_name in parent_loop_cache:
            return parent_loop_cache[parent_name]
        cur = conn.execute("""
            SELECT input, output, metadata
            FROM traces
            WHERE request_id = ? AND node_name = ? AND status = 'writing'
            LIMIT 1
        """, (request_id, parent_name))
        r = cur.fetchone()
        if r:
            try:
                inp = json.loads(r[0]) if r[0] else {}
                out = json.loads(r[1]) if r[1] else {}
                meta = json.loads(r[2]) if r[2] else {}
                parent_loop_cache[parent_name] = {'input': inp, 'output': out, 'metadata': meta}
            except (json.JSONDecodeError, TypeError):
                parent_loop_cache[parent_name] = None
        else:
            parent_loop_cache[parent_name] = None
        return parent_loop_cache[parent_name]

    # Group nodes by (parent_name, context_id)
    # Key: (parent_name, context_id) -> iteration group
    iteration_groups: Dict[Tuple[str, str], Dict] = {}

    for row in rows:
        (node_id, node_name, parent_name, context_id, start_time, end_time,
         duration_ms, workflow_name, user_id, session_id,
         prompt_tokens, completion_tokens, total_tokens, cost_usd) = row

        if not context_id or not parent_name:
            continue

        # Use full context_id as the grouping key
        # All nodes with same (parent_name, context_id) go into same iteration group
        key = (parent_name, context_id)

        if key not in iteration_groups:
            iteration_groups[key] = {
                'parent_name': parent_name,
                'context_id': context_id,
                'children': [],
                'start_time': start_time,
                'end_time': end_time,
                'workflow_name': workflow_name,
                'user_id': user_id,
                'session_id': session_id,
                'prompt_tokens': 0,
                'completion_tokens': 0,
                'total_tokens': 0,
                'cost_usd': 0.0,
            }

        group = iteration_groups[key]
        group['children'].append(node_id)

        # Aggregate tokens and cost
        if prompt_tokens:
            group['prompt_tokens'] += prompt_tokens
        if completion_tokens:
            group['completion_tokens'] += completion_tokens
        if total_tokens:
            group['total_tokens'] += total_tokens
        if cost_usd:
            group['cost_usd'] += cost_usd

        # Update time bounds
        if start_time and (not group['start_time'] or start_time < group['start_time']):
            group['start_time'] = start_time
        if end_time and (not group['end_time'] or end_time > group['end_time']):
            group['end_time'] = end_time

    # Create iteration group traces
    now = time()

    for (parent_name, context_id), group in iteration_groups.items():
        # Extract the last index from context_id for parent_context calculation
        # e.g., [0] -> parent_context=None, [0].[1] -> parent_context=[0]
        last_bracket_start = context_id.rfind('[')
        if last_bracket_start == -1:
            continue

        # The iteration group's context is the parent's context (everything before last bracket)
        parent_context = context_id[:last_bracket_start].rstrip('.') if last_bracket_start > 0 else None

        # Use full context_id for iteration name: iteration[0], iteration[0][1], iteration[0][1][2]
        # Remove dots from context_id: [0].[1] -> [0][1]
        iteration_suffix = context_id.replace('.', '')
        iteration_name = f"{parent_name}.iteration{iteration_suffix}"

        # Calculate duration
        duration_ms = None
        if group['start_time'] and group['end_time']:
            try:
                from datetime import datetime
                start = datetime.fromisoformat(group['start_time'])
                end = datetime.fromisoformat(group['end_time'])
                duration_ms = (end - start).total_seconds() * 1000
            except (ValueError, TypeError):
                pass

        # Extract iteration index from context_id (e.g., [0] -> 0, [0].[1] -> last is 1)
        last_bracket = context_id[last_bracket_start:]
        try:
            iteration_index = int(last_bracket.strip('[]'))
        except (ValueError, TypeError):
            iteration_index = None

        # Slice input/output from parent loop node at iteration_index
        iter_input = None
        iter_output = None

        if iteration_index is not None:
            parent_data = _get_parent_loop(parent_name)
            if parent_data:
                parent_input = parent_data['input']
                parent_output = parent_data['output']
                parent_meta = parent_data['metadata']

                # Input: slice each "each" field at iteration_index
                # metadata.each tells us which input fields are iterated
                each_fields = parent_meta.get('each', [])
                sliced_input = {}
                for field_name, field_value in parent_input.items():
                    if field_name in each_fields and isinstance(field_value, list):
                        # Per-iteration value: slice at index
                        if iteration_index < len(field_value):
                            sliced_input[field_name] = field_value[iteration_index]
                    else:
                        # Shared value across all iterations
                        sliced_input[field_name] = field_value
                if sliced_input:
                    iter_input = json.dumps(sliced_input)

                # Output: slice array outputs at iteration_index
                # Loop outputs are collected as arrays, one element per iteration
                sliced_output = {}
                for field_name, field_value in parent_output.items():
                    if field_name.startswith('$') or field_name == 'iteration_metrics':
                        continue
                    if isinstance(field_value, list) and iteration_index < len(field_value):
                        sliced_output[field_name] = field_value[iteration_index]
                    else:
                        sliced_output[field_name] = field_value
                if sliced_output:
                    iter_output = json.dumps(sliced_output)

        # Build iteration metadata
        iter_metadata = {
            '_synthetic': True,
            '_iteration_group': True,
            'iteration_index': iteration_index,
            'children_count': len(group['children']),
        }

        # Insert iteration group trace
        conn.execute("""
            INSERT INTO traces (
                request_id, workflow_name, node_name, node_type, parent_name, context_id,
                execution_order, start_time, end_time, duration_ms,
                prompt_tokens, completion_tokens, total_tokens, cost_usd,
                input, output,
                user_id, session_id, contain_generation, metadata,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, -1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'writing', ?)
        """, (
            request_id,
            group['workflow_name'],
            iteration_name,
            'iteration',
            parent_name,
            parent_context,
            group['start_time'],
            group['end_time'],
            duration_ms,
            group['prompt_tokens'] or None,
            group['completion_tokens'] or None,
            group['total_tokens'] or None,
            group['cost_usd'] or None,
            iter_input,
            iter_output,
            group['user_id'],
            group['session_id'],
            json.dumps(iter_metadata),
            now,
        ))

        # Update children to point to this iteration group
        child_ids = group['children']
        if child_ids:
            placeholders = ','.join('?' * len(child_ids))
            conn.execute(f"""
                UPDATE traces
                SET parent_name = ?
                WHERE id IN ({placeholders})
            """, [iteration_name] + child_ids)

    conn.commit()


def _mark_complete(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    """Mark traces as ready for flushing or flushed for local tracers."""
    tracer_type = data["tracer_type"]
    tracer_config_json = json.dumps(data.get("tracer_config", {}))
    request_id = data["request_id"]
    tags = data.get("tags")
    tags_json = json.dumps(tags) if tags else None

    # Create synthetic iteration groups before finalizing
    _create_iteration_groups(conn, request_id)

    # LocalTracer doesn't need external flushing - mark as flushed directly
    if tracer_type == "LocalTracer":
        conn.execute("""
            UPDATE traces
            SET status = 'flushed', tracer_type = ?, tracer_config = ?, tags = ?, flushed_at = ?
            WHERE request_id = ? AND status = 'writing'
        """, (tracer_type, tracer_config_json, tags_json, time(), request_id))
    else:
        # Other tracers need the background flush loop
        conn.execute("""
            UPDATE traces
            SET status = 'pending', tracer_type = ?, tracer_config = ?, tags = ?
            WHERE request_id = ? AND status = 'writing'
        """, (tracer_type, tracer_config_json, tags_json, request_id))
    conn.commit()


def _fetch_pending(conn: sqlite3.Connection, limit: int = 50) -> Dict[str, List[sqlite3.Row]]:
    """Fetch pending traces grouped by request_id."""
    cursor = conn.execute("""
        SELECT DISTINCT request_id FROM traces
        WHERE status = 'pending'
        ORDER BY created_at
        LIMIT ?
    """, (limit,))
    request_ids = [row[0] for row in cursor.fetchall()]

    if not request_ids:
        return {}

    placeholders = ",".join("?" * len(request_ids))
    cursor = conn.execute(f"""
        SELECT * FROM traces
        WHERE request_id IN ({placeholders})
        ORDER BY request_id, execution_order
    """, request_ids)

    result: Dict[str, List[sqlite3.Row]] = {}
    for row in cursor.fetchall():
        rid = row["request_id"]
        if rid not in result:
            result[rid] = []
        result[rid].append(row)

    return result


def _rebuild_flush_data(rows: List[sqlite3.Row]) -> Dict[str, Any]:
    """Rebuild flush_data structure from flattened rows.

    Importantly, this function reorders nodes so that parents come before children,
    which is required for Langfuse hierarchy creation.
    """
    if not rows:
        return {}

    first_row = rows[0]
    flush_data = {
        "tracer_type": first_row["tracer_type"],
        "tracer_config": json.loads(first_row["tracer_config"]) if first_row["tracer_config"] else {},
        "workflow_name": first_row["workflow_name"],
        "request_id": first_row["request_id"],
        "user_id": first_row["user_id"],
        "session_id": first_row["session_id"],
        "tags": json.loads(first_row["tags"]) if first_row["tags"] else [],
        "execution_order": [],
        "nodes_trace_data": {},
    }

    # Build node data first
    node_data_map = {}
    for row in rows:
        node_name = row["node_name"]
        context_id = row["context_id"]
        trace_key = f"{node_name}:{context_id}" if context_id else node_name

        # Skip duplicates (keep first occurrence)
        if trace_key in node_data_map:
            continue

        usage = None
        if row["prompt_tokens"] is not None or row["completion_tokens"] is not None:
            usage = {}
            if row["prompt_tokens"] is not None:
                usage["prompt_tokens"] = row["prompt_tokens"]
            if row["completion_tokens"] is not None:
                usage["completion_tokens"] = row["completion_tokens"]
            if row["total_tokens"] is not None:
                usage["total_tokens"] = row["total_tokens"]

        node_data_map[trace_key] = {
            "node": node_name,
            "parent": row["parent_name"],
            "context_id": context_id,
            "contain_generation": bool(row["contain_generation"]),
            "trace_data": {
                "name": node_name,
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "input": json.loads(row["input"]) if row["input"] else {},
                "output": json.loads(row["output"]) if row["output"] else {},
                "model": row["model"],
                "usage": usage,
                "cost": row["cost_usd"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            }
        }

    # Topological sort: parents before children
    # Build adjacency: parent_key -> [child_keys]
    def get_parent_key(node_name: str, parent_name: Optional[str], context_id: Optional[str]) -> Optional[str]:
        """Get the key for the parent node."""
        if parent_name is None:
            return None
        # Check if parent exists with same context_id
        parent_with_ctx = f"{parent_name}:{context_id}" if context_id else parent_name
        if parent_with_ctx in node_data_map:
            return parent_with_ctx
        # Check if parent exists without context
        if parent_name in node_data_map:
            return parent_name
        return parent_name  # Return even if not found, for ordering

    # Sort nodes: roots first, then by depth
    ordered_keys = []
    visited = set()

    def visit(key: str):
        if key in visited:
            return
        visited.add(key)
        data = node_data_map.get(key)
        if data:
            parent_key = get_parent_key(data["node"], data["parent"], data["context_id"])
            if parent_key and parent_key in node_data_map:
                visit(parent_key)
        ordered_keys.append(key)

    # Visit all nodes
    for key in node_data_map:
        visit(key)

    # Build execution_order and nodes_trace_data in correct order
    for key in ordered_keys:
        data = node_data_map[key]
        flush_data["execution_order"].append({
            "node": data["node"],
            "parent": data["parent"],
            "context_id": data["context_id"],
            "contain_generation": data["contain_generation"],
        })
        flush_data["nodes_trace_data"][key] = data["trace_data"]

    return flush_data


def _dispatch_flush(flush_data: Dict[str, Any]) -> None:
    """Dispatch flush to the appropriate tracer."""
    from hush.core.tracers.base import _TRACER_REGISTRY

    tracer_type = flush_data.get("tracer_type")

    if tracer_type not in _TRACER_REGISTRY:
        try:
            # Import hush.observability to register all external tracers
            # This registers LangfuseTracer, OTELTracer and their backends
            import hush.observability  # noqa: F401
        except ImportError:
            pass

    tracer_cls = _TRACER_REGISTRY.get(tracer_type)
    if tracer_cls is None:
        print(f"[BackgroundWorker] Unknown tracer type: {tracer_type}")
        return

    tracer_cls.flush(flush_data)


def _background_worker(
    task_queue: multiprocessing.Queue,
    db_path: str,
    config_path: Optional[str],
    dotenv_path: Optional[str],
    poll_interval: float = 2.0,
    batch_size: int = 10,
    max_retries: int = 3,
) -> None:
    """Background worker process that handles all non-main-flow tasks.

    Args:
        task_queue: Queue for receiving tasks from main process
        db_path: Path to SQLite database
        config_path: Absolute path to resources.yaml
        dotenv_path: Absolute path to .env file
        poll_interval: Seconds between flush polls
        batch_size: Max requests per flush poll
        max_retries: Max retry attempts for failed traces
    """
    import os
    import traceback

    # Ignore SIGINT - let main process handle it
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    # Load .env file if provided
    if dotenv_path:
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path)
        except ImportError:
            pass

    # Set HUSH_CONFIG env var
    if config_path:
        os.environ['HUSH_CONFIG'] = config_path

    # Initialize ResourceHub
    try:
        from hush.core.registry import get_hub
        get_hub()
    except Exception as e:
        print(f"[BackgroundWorker] Failed to initialize ResourceHub: {e}")

    # Initialize database
    conn = _init_db(Path(db_path))
    print(f"[BackgroundWorker] Started, db={db_path}")

    last_flush_time = time()
    running = True

    while running:
        try:
            # Process tasks from queue (non-blocking with timeout)
            try:
                while True:
                    # Check for tasks with short timeout
                    try:
                        task_data = task_queue.get(timeout=0.1)
                    except Exception:
                        break

                    task_type = task_data.get("task_type")
                    data = task_data.get("data", {})

                    if task_type == TaskType.SHUTDOWN.value:
                        print("[BackgroundWorker] Received shutdown signal")
                        running = False
                        break
                    elif task_type == TaskType.TRACE_WRITE.value:
                        _write_trace(conn, data)
                    elif task_type == TaskType.TRACE_COMPLETE.value:
                        _mark_complete(conn, data)

            except Exception as e:
                print(f"[BackgroundWorker] Error processing queue: {e}")

            if not running:
                break

            # Periodic flush check
            now = time()
            if now - last_flush_time >= poll_interval:
                last_flush_time = now

                # Retry failed traces
                try:
                    conn.execute("""
                        UPDATE traces SET status = 'pending', error = NULL
                        WHERE status = 'failed' AND retry_count < ?
                    """, (max_retries,))
                    conn.commit()
                except Exception:
                    pass

                # Fetch and flush pending traces
                try:
                    pending = _fetch_pending(conn, limit=batch_size)

                    for request_id, rows in pending.items():
                        try:
                            # Mark as flushing
                            conn.execute("""
                                UPDATE traces SET status = 'flushing'
                                WHERE request_id = ? AND status = 'pending'
                            """, (request_id,))
                            conn.commit()

                            # Rebuild and dispatch
                            flush_data = _rebuild_flush_data(rows)
                            _dispatch_flush(flush_data)

                            # Mark as flushed
                            conn.execute("""
                                UPDATE traces SET status = 'flushed', flushed_at = ?
                                WHERE request_id = ?
                            """, (time(), request_id))
                            conn.commit()

                        except Exception as e:
                            error_msg = traceback.format_exc()
                            print(f"[BackgroundWorker] Error flushing {request_id}: {e}")
                            conn.execute("""
                                UPDATE traces
                                SET status = 'failed', error = ?, retry_count = retry_count + 1
                                WHERE request_id = ?
                            """, (error_msg, request_id))
                            conn.commit()

                except Exception as e:
                    print(f"[BackgroundWorker] Error in flush loop: {e}")

            # Small sleep to prevent busy loop
            sleep(0.05)

        except Exception as e:
            print(f"[BackgroundWorker] Unexpected error: {e}\n{traceback.format_exc()}")
            sleep(1)

    # Cleanup
    conn.close()
    print("[BackgroundWorker] Stopped")


class BackgroundProcess:
    """Manages the unified background worker process.

    This class provides a simple interface for submitting tasks to the
    background process. The process is started lazily on first use.

    Thread-safe: Multiple threads can submit tasks concurrently.
    """

    __slots__ = ['_db_path', '_process', '_queue', '_lock', '_started']

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize BackgroundProcess.

        Args:
            db_path: Path to SQLite database. Defaults to HUSH_TRACES_DB env var
                     or ~/.hush/traces.db if not set.
        """
        self._db_path = db_path or DEFAULT_DB_PATH
        self._process: Optional[multiprocessing.Process] = None
        self._queue: Optional[multiprocessing.Queue] = None
        self._lock = threading.Lock()
        self._started = False

    def _ensure_started(self) -> None:
        """Ensure the background process is running."""
        if self._started and self._process is not None and self._process.is_alive():
            return

        with self._lock:
            if self._started and self._process is not None and self._process.is_alive():
                return

            # Get config path from current hub's storage
            config_path = None
            try:
                from hush.core.registry import get_hub
                hub = get_hub()
                if hasattr(hub._storage, '_file_path'):
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

            # Create queue and process
            ctx = multiprocessing.get_context("spawn")
            self._queue = ctx.Queue()
            self._process = ctx.Process(
                target=_background_worker,
                args=(self._queue, str(self._db_path), config_path, dotenv_path),
                daemon=True,
            )
            self._process.start()
            self._started = True

    def submit(self, task_type: TaskType, data: Dict[str, Any]) -> None:
        """Submit a task to the background process.

        Args:
            task_type: Type of task
            data: Task data
        """
        self._ensure_started()
        self._queue.put({"task_type": task_type.value, "data": data})

    def write_trace(
        self,
        request_id: str,
        workflow_name: str,
        node_name: str,
        node_type: Optional[str] = None,
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
        """Write a trace to the background process (non-blocking).

        Args:
            All trace fields - see insert_node_trace for details
        """
        self.submit(TaskType.TRACE_WRITE, {
            "request_id": request_id,
            "workflow_name": workflow_name,
            "node_name": node_name,
            "node_type": node_type,
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
        })

    def mark_complete(
        self,
        request_id: str,
        tracer_type: str,
        tracer_config: Dict[str, Any],
        tags: Optional[List[str]] = None,
    ) -> None:
        """Mark traces as ready for flushing (non-blocking).

        Args:
            request_id: The request to mark complete
            tracer_type: Type of tracer
            tracer_config: Tracer configuration
            tags: Optional list of tags for filtering/grouping traces
        """
        self.submit(TaskType.TRACE_COMPLETE, {
            "request_id": request_id,
            "tracer_type": tracer_type,
            "tracer_config": tracer_config,
            "tags": tags,
        })

    def shutdown(self, timeout: float = 5.0) -> None:
        """Shutdown the background process gracefully.

        Args:
            timeout: Maximum time to wait for shutdown
        """
        if self._process is None or not self._process.is_alive():
            return

        # Send shutdown signal
        try:
            self._queue.put({"task_type": TaskType.SHUTDOWN.value, "data": {}})
        except Exception:
            pass

        # Wait for process to finish
        self._process.join(timeout=timeout)
        if self._process.is_alive():
            self._process.terminate()

        self._process = None
        self._queue = None
        self._started = False

    @property
    def is_running(self) -> bool:
        """Check if background process is running."""
        return self._process is not None and self._process.is_alive()

    @property
    def db_path(self) -> Path:
        """Get database path."""
        return self._db_path


# Global background process instance
_background: Optional[BackgroundProcess] = None
_background_lock = threading.Lock()


def get_background(db_path: Optional[Path] = None) -> BackgroundProcess:
    """Get global BackgroundProcess instance.

    Args:
        db_path: Optional path to database

    Returns:
        BackgroundProcess instance
    """
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