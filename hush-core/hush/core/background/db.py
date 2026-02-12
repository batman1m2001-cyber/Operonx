"""SQLite database operations for background trace storage.

This module handles all SQLite interactions:
- Database initialization with fallback chain (primary → tempdir → in-memory)
- Trace writing, completion marking, and pending trace fetching
- Iteration group creation for loop nodes
"""

import os
import sqlite3
import tempfile
from pathlib import Path
from time import time
from typing import Any, Dict, List, Optional, Tuple

import orjson

# Max JSON payload size (1MB) — larger payloads are truncated to prevent DB bloat
_MAX_JSON_SIZE = 1024 * 1024


def _json_dumps(obj: Any) -> Optional[str]:
    """Serialize to JSON string using orjson. Returns None for None input.

    Truncates payloads exceeding 1MB with a marker to prevent DB bloat
    from large LLM responses or embedding vectors.
    """
    if obj is None:
        return None
    try:
        raw = orjson.dumps(obj).decode()
        if len(raw) > _MAX_JSON_SIZE:
            return raw[: _MAX_JSON_SIZE - 50] + "...[TRUNCATED]"
        return raw
    except (TypeError, ValueError):
        return f"<non-serializable: {type(obj).__name__}>"


# Default database path - can be overridden via HUSH_TRACES_DB env var
DEFAULT_DB_PATH = Path(os.environ.get("HUSH_TRACES_DB", Path.home() / ".hush" / "traces.db"))

_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS traces (
        -- Identity
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,
        workflow_name TEXT NOT NULL,

        -- Node identity
        op_name TEXT,
        op_type TEXT,
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
"""


def _connect_and_init(db_path_str: str) -> sqlite3.Connection:
    """Connect to SQLite and initialize schema. Raises on failure."""
    if db_path_str != ":memory:":
        Path(db_path_str).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path_str, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA_SQL)

    # Migration: Add columns if they don't exist (for databases created before these features)
    cursor = conn.execute("PRAGMA table_info(traces)")
    columns = {row[1] for row in cursor.fetchall()}
    if "tags" not in columns:
        conn.execute("ALTER TABLE traces ADD COLUMN tags TEXT")
    if "op_type" not in columns:
        conn.execute("ALTER TABLE traces ADD COLUMN op_type TEXT")

    conn.commit()
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize SQLite database with fallback chain.

    Tries:
        1. Primary path (db_path)
        2. Temp directory fallback
        3. In-memory database

    Args:
        db_path: Preferred database path

    Returns:
        SQLite connection (always succeeds)
    """
    # 1. Try primary path
    try:
        return _connect_and_init(str(db_path))
    except Exception as e:
        print(f"[BackgroundWorker] Cannot create DB at {db_path}: {e}")

    # 2. Try temp directory
    try:
        tmp_dir = tempfile.mkdtemp(prefix="hush_")
        tmp_path = os.path.join(tmp_dir, "traces.db")
        print(f"[BackgroundWorker] Falling back to temp DB: {tmp_path}")
        return _connect_and_init(tmp_path)
    except Exception as e:
        print(f"[BackgroundWorker] Cannot create temp DB: {e}")

    # 3. In-memory (always works, traces lost on restart but external flush still works)
    print("[BackgroundWorker] Falling back to in-memory DB (traces will not persist)")
    return _connect_and_init(":memory:")


def _prepare_trace_row(data: Dict[str, Any], now: float) -> tuple:
    """Prepare a single trace row tuple for insertion."""
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

    return (
        data["request_id"],
        data["workflow_name"],
        data["op_name"],
        data.get("op_type"),
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
        _json_dumps(data.get("input_data")),
        _json_dumps(data.get("output_data")),
        data.get("user_id"),
        data.get("session_id"),
        1 if data.get("contain_generation") else 0,
        _json_dumps(data.get("metadata")),
        now,
    )


_INSERT_SQL = """
    INSERT INTO traces (
        request_id, workflow_name, op_name, op_type, parent_name, context_id,
        execution_order, start_time, end_time, duration_ms,
        model, prompt_tokens, completion_tokens, total_tokens, cost_usd,
        input, output, user_id, session_id,
        contain_generation, metadata, status, retry_count, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'writing', 0, ?)
"""


def write_traces_batch(conn: sqlite3.Connection, traces: List[Dict[str, Any]]) -> None:
    """Write multiple traces in a single transaction (batch optimization).

    Uses executemany + single commit instead of individual execute+commit per trace.
    """
    if not traces:
        return
    now = time()
    rows = [_prepare_trace_row(data, now) for data in traces]
    conn.executemany(_INSERT_SQL, rows)
    conn.commit()


def write_trace(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    """Write a single trace to database."""
    write_traces_batch(conn, [data])


def create_iteration_groups(conn: sqlite3.Connection, request_id: str) -> None:
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
    cursor = conn.execute(
        """
        SELECT id, op_name, parent_name, context_id, start_time, end_time,
               duration_ms, workflow_name, user_id, session_id,
               prompt_tokens, completion_tokens, total_tokens, cost_usd
        FROM traces
        WHERE request_id = ? AND status = 'writing'
        ORDER BY execution_order
    """,
        (request_id,),
    )
    rows = cursor.fetchall()

    if not rows:
        return

    # Build a lookup of parent loop nodes (with input/output/metadata)
    # to slice per-iteration values
    parent_loop_cache: Dict[str, Dict] = {}

    def _get_parent_loop(parent_name: str) -> Optional[Dict]:
        if parent_name in parent_loop_cache:
            return parent_loop_cache[parent_name]
        cur = conn.execute(
            """
            SELECT input, output, metadata
            FROM traces
            WHERE request_id = ? AND op_name = ? AND status = 'writing'
            LIMIT 1
        """,
            (request_id, parent_name),
        )
        r = cur.fetchone()
        if r:
            try:
                inp = orjson.loads(r[0]) if r[0] else {}
                out = orjson.loads(r[1]) if r[1] else {}
                meta = orjson.loads(r[2]) if r[2] else {}
                parent_loop_cache[parent_name] = {"input": inp, "output": out, "metadata": meta}
            except (orjson.JSONDecodeError, TypeError):
                parent_loop_cache[parent_name] = None
        else:
            parent_loop_cache[parent_name] = None
        return parent_loop_cache[parent_name]

    # Group nodes by (parent_name, context_id)
    iteration_groups: Dict[Tuple[str, str], Dict] = {}

    for row in rows:
        (
            op_id,
            op_name,
            parent_name,
            context_id,
            start_time,
            end_time,
            duration_ms,
            workflow_name,
            user_id,
            session_id,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            cost_usd,
        ) = row

        if not context_id or not parent_name:
            continue

        key = (parent_name, context_id)

        if key not in iteration_groups:
            iteration_groups[key] = {
                "parent_name": parent_name,
                "context_id": context_id,
                "children": [],
                "start_time": start_time,
                "end_time": end_time,
                "workflow_name": workflow_name,
                "user_id": user_id,
                "session_id": session_id,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
            }

        group = iteration_groups[key]
        group["children"].append(op_id)

        # Aggregate tokens and cost
        if prompt_tokens:
            group["prompt_tokens"] += prompt_tokens
        if completion_tokens:
            group["completion_tokens"] += completion_tokens
        if total_tokens:
            group["total_tokens"] += total_tokens
        if cost_usd:
            group["cost_usd"] += cost_usd

        # Update time bounds
        if start_time and (not group["start_time"] or start_time < group["start_time"]):
            group["start_time"] = start_time
        if end_time and (not group["end_time"] or end_time > group["end_time"]):
            group["end_time"] = end_time

    # Create iteration group traces
    now = time()

    for (parent_name, context_id), group in iteration_groups.items():
        last_bracket_start = context_id.rfind("[")
        if last_bracket_start == -1:
            continue

        parent_context = (
            context_id[:last_bracket_start].rstrip(".") if last_bracket_start > 0 else None
        )

        iteration_suffix = context_id.replace(".", "")
        iteration_name = f"{parent_name}.iteration{iteration_suffix}"

        # Calculate duration
        duration_ms = None
        if group["start_time"] and group["end_time"]:
            try:
                from datetime import datetime

                start = datetime.fromisoformat(group["start_time"])
                end = datetime.fromisoformat(group["end_time"])
                duration_ms = (end - start).total_seconds() * 1000
            except (ValueError, TypeError):
                pass

        last_bracket = context_id[last_bracket_start:]
        try:
            iteration_index = int(last_bracket.strip("[]"))
        except (ValueError, TypeError):
            iteration_index = None

        # Slice input/output from parent loop node at iteration_index
        iter_input = None
        iter_output = None

        if iteration_index is not None:
            parent_data = _get_parent_loop(parent_name)
            if parent_data:
                parent_input = parent_data["input"]
                parent_output = parent_data["output"]
                parent_meta = parent_data["metadata"]

                each_fields = parent_meta.get("each", [])
                sliced_input = {}
                for field_name, field_value in parent_input.items():
                    if field_name in each_fields and isinstance(field_value, list):
                        if iteration_index < len(field_value):
                            sliced_input[field_name] = field_value[iteration_index]
                    else:
                        sliced_input[field_name] = field_value
                if sliced_input:
                    iter_input = _json_dumps(sliced_input)

                sliced_output = {}
                for field_name, field_value in parent_output.items():
                    if field_name.startswith("$") or field_name == "iteration_metrics":
                        continue
                    if isinstance(field_value, list) and iteration_index < len(field_value):
                        sliced_output[field_name] = field_value[iteration_index]
                    else:
                        sliced_output[field_name] = field_value
                if sliced_output:
                    iter_output = _json_dumps(sliced_output)

        iter_metadata = {
            "_synthetic": True,
            "_iteration_group": True,
            "iteration_index": iteration_index,
            "children_count": len(group["children"]),
        }

        conn.execute(
            """
            INSERT INTO traces (
                request_id, workflow_name, op_name, op_type, parent_name, context_id,
                execution_order, start_time, end_time, duration_ms,
                prompt_tokens, completion_tokens, total_tokens, cost_usd,
                input, output,
                user_id, session_id, contain_generation, metadata,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, -1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'writing', ?)
        """,
            (
                request_id,
                group["workflow_name"],
                iteration_name,
                "iteration",
                parent_name,
                parent_context,
                group["start_time"],
                group["end_time"],
                duration_ms,
                group["prompt_tokens"] or None,
                group["completion_tokens"] or None,
                group["total_tokens"] or None,
                group["cost_usd"] or None,
                iter_input,
                iter_output,
                group["user_id"],
                group["session_id"],
                _json_dumps(iter_metadata),
                now,
            ),
        )

        # Update children to point to this iteration group
        child_ids = group["children"]
        if child_ids:
            placeholders = ",".join("?" * len(child_ids))
            conn.execute(
                f"""
                UPDATE traces
                SET parent_name = ?
                WHERE id IN ({placeholders})
            """,
                [iteration_name] + child_ids,
            )

    conn.commit()


def mark_complete(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    """Mark traces as ready for flushing or flushed for local tracers."""
    tracer_type = data["tracer_type"]
    tracer_config_json = _json_dumps(data.get("tracer_config", {}))
    request_id = data["request_id"]
    tags = data.get("tags")
    tags_json = _json_dumps(tags)

    # Create synthetic iteration groups before finalizing
    create_iteration_groups(conn, request_id)

    # LocalTracer doesn't need external flushing - mark as flushed directly
    if tracer_type == "LocalTracer":
        conn.execute(
            """
            UPDATE traces
            SET status = 'flushed', tracer_type = ?, tracer_config = ?, tags = ?, flushed_at = ?
            WHERE request_id = ? AND status = 'writing'
        """,
            (tracer_type, tracer_config_json, tags_json, time(), request_id),
        )
    else:
        conn.execute(
            """
            UPDATE traces
            SET status = 'pending', tracer_type = ?, tracer_config = ?, tags = ?
            WHERE request_id = ? AND status = 'writing'
        """,
            (tracer_type, tracer_config_json, tags_json, request_id),
        )
    conn.commit()


def fetch_pending(conn: sqlite3.Connection, limit: int = 50) -> Dict[str, List[sqlite3.Row]]:
    """Fetch pending traces grouped by request_id."""
    cursor = conn.execute(
        """
        SELECT DISTINCT request_id FROM traces
        WHERE status = 'pending'
        ORDER BY created_at
        LIMIT ?
    """,
        (limit,),
    )
    request_ids = [row[0] for row in cursor.fetchall()]

    if not request_ids:
        return {}

    placeholders = ",".join("?" * len(request_ids))
    cursor = conn.execute(
        f"""
        SELECT * FROM traces
        WHERE request_id IN ({placeholders})
        ORDER BY request_id, execution_order
    """,
        request_ids,
    )

    result: Dict[str, List[sqlite3.Row]] = {}
    for row in cursor.fetchall():
        rid = row["request_id"]
        if rid not in result:
            result[rid] = []
        result[rid].append(row)

    return result
