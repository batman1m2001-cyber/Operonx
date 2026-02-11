"""Tests for background process package: subprocess fallback, SQLite resilience, and performance."""

import json
import sqlite3
import tempfile
import time
from pathlib import Path
from time import sleep
from unittest.mock import patch

import pytest

from hush.core import END, PARENT, START, GraphNode, Hush
from hush.core.background import BackgroundProcess, shutdown_background
from hush.core.background.db import init_db
from hush.core.nodes import CodeNode
from hush.core.tracers import LocalTracer


class TestSQLiteResilience:
    """Test SQLite DB creation fallback chain."""

    def test_init_db_primary_path(self):
        """Test normal DB creation at primary path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "subdir" / "traces.db"
            conn = init_db(db_path)
            assert conn is not None

            # Verify schema exists
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            assert "traces" in tables
            conn.close()

    def test_init_db_fallback_to_tempdir(self):
        """Test fallback to temp directory when primary path fails."""
        # Use a path that can't be created (invalid chars on Windows, nonexistent root on Unix)
        impossible_path = Path("/nonexistent_root_zzzz/impossible/traces.db")
        conn = init_db(impossible_path)
        assert conn is not None

        # Verify it works (schema created)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "traces" in tables
        conn.close()

    def test_init_db_fallback_to_memory(self):
        """Test fallback to in-memory DB when both primary and temp fail."""
        with patch("hush.core.background.db.tempfile.mkdtemp", side_effect=OSError("mocked")):
            impossible_path = Path("/nonexistent_root_zzzz/impossible/traces.db")
            conn = init_db(impossible_path)
            assert conn is not None

            # Verify schema exists in memory DB
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            assert "traces" in tables
            conn.close()

    def test_init_db_migration_adds_missing_columns(self):
        """Test that init_db adds missing columns to existing DBs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "old.db"

            # Create a DB with old schema (missing tags and node_type)
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    workflow_name TEXT NOT NULL,
                    node_name TEXT,
                    parent_name TEXT,
                    context_id TEXT,
                    execution_order INTEGER,
                    start_time TEXT,
                    end_time TEXT,
                    duration_ms REAL,
                    model TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    total_tokens INTEGER,
                    cost_usd REAL,
                    input TEXT,
                    output TEXT,
                    user_id TEXT,
                    session_id TEXT,
                    tracer_type TEXT,
                    tracer_config TEXT,
                    contain_generation INTEGER DEFAULT 0,
                    metadata TEXT,
                    status TEXT DEFAULT 'pending',
                    retry_count INTEGER DEFAULT 0,
                    created_at REAL NOT NULL,
                    flushed_at REAL,
                    error TEXT
                )
            """)
            conn.commit()
            conn.close()

            # Re-init should add missing columns
            conn = init_db(db_path)
            cursor = conn.execute("PRAGMA table_info(traces)")
            columns = {row[1] for row in cursor.fetchall()}
            assert "tags" in columns
            assert "node_type" in columns
            conn.close()


def _force_subprocess_mode(bg: BackgroundProcess) -> None:
    """Force a BackgroundProcess instance to use subprocess fallback.

    Patches BackgroundProcess._start_multiprocessing at the class level to raise
    AssertionError (simulating a daemon worker), then calls _ensure_started().
    """
    original = BackgroundProcess._start_multiprocessing

    def _raise_assertion(self, *args, **kwargs):
        raise AssertionError("daemonic processes are not allowed to have children")

    BackgroundProcess._start_multiprocessing = _raise_assertion
    try:
        bg._ensure_started()
    finally:
        BackgroundProcess._start_multiprocessing = original


class TestSubprocessFallback:
    """Test subprocess.Popen fallback when multiprocessing.Process is blocked."""

    def test_subprocess_fallback_on_daemon_assertion(self):
        """Test that BackgroundProcess falls back to subprocess when multiprocessing raises AssertionError."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "subprocess_test.db"
            bg = BackgroundProcess(db_path)

            try:
                _force_subprocess_mode(bg)

                # Should have fallen back to subprocess
                assert bg._is_subprocess is True
                assert bg.is_running

                # Write a trace through the subprocess fallback
                bg.write_trace(
                    request_id="subprocess-test-001",
                    workflow_name="test-wf",
                    node_name="test-node",
                )

                # Poll until trace appears (subprocess startup can be slow on CI)
                rows = []
                for _ in range(50):  # up to 5 seconds
                    sleep(0.1)
                    try:
                        conn = sqlite3.connect(str(db_path))
                        conn.row_factory = sqlite3.Row
                        cursor = conn.execute(
                            "SELECT * FROM traces WHERE request_id = ?",
                            ("subprocess-test-001",),
                        )
                        rows = cursor.fetchall()
                        conn.close()
                    except sqlite3.OperationalError:
                        # Table not yet created by subprocess — keep polling
                        continue
                    if len(rows) == 1:
                        break

                assert len(rows) == 1
                assert rows[0]["node_name"] == "test-node"
            finally:
                bg.shutdown()

    def test_subprocess_fallback_mark_complete(self):
        """Test mark_complete works through subprocess fallback."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "subprocess_complete.db"
            bg = BackgroundProcess(db_path)

            try:
                _force_subprocess_mode(bg)

                assert bg._is_subprocess is True

                # Write and complete
                bg.write_trace(
                    request_id="sub-complete-001",
                    workflow_name="test-wf",
                    node_name="node-1",
                    execution_order=0,
                )

                # Poll until trace is written
                for _ in range(50):
                    sleep(0.1)
                    try:
                        conn = sqlite3.connect(str(db_path))
                        conn.row_factory = sqlite3.Row
                        row = conn.execute(
                            "SELECT * FROM traces WHERE request_id = ?",
                            ("sub-complete-001",),
                        ).fetchone()
                        conn.close()
                    except sqlite3.OperationalError:
                        continue
                    if row is not None:
                        break

                bg.mark_complete(
                    request_id="sub-complete-001",
                    tracer_type="LocalTracer",
                    tracer_config={"name": "test"},
                    tags=["subprocess-test"],
                )

                # Poll until mark_complete is processed
                row = None
                for _ in range(50):
                    sleep(0.1)
                    try:
                        conn = sqlite3.connect(str(db_path))
                        conn.row_factory = sqlite3.Row
                        row = conn.execute(
                            "SELECT status, tags FROM traces WHERE request_id = ?",
                            ("sub-complete-001",),
                        ).fetchone()
                        conn.close()
                    except sqlite3.OperationalError:
                        continue
                    if row and row["status"] == "flushed":
                        break

                assert row["status"] == "flushed"
                assert json.loads(row["tags"]) == ["subprocess-test"]
            finally:
                bg.shutdown()

    def test_subprocess_shutdown_closes_pipe(self):
        """Test shutdown properly closes stdin pipe for subprocess mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "shutdown_test.db"
            bg = BackgroundProcess(db_path)

            _force_subprocess_mode(bg)

            assert bg.is_running
            bg.shutdown()
            assert not bg.is_running

    def test_disabled_when_both_strategies_fail(self):
        """Test _disabled=True only when BOTH multiprocessing and subprocess fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "both_fail.db"
            bg = BackgroundProcess(db_path)

            original_mp = BackgroundProcess._start_multiprocessing
            original_sp = BackgroundProcess._start_subprocess

            def _raise_assertion(self, *a, **kw):
                raise AssertionError("daemonic")

            def _raise_os_error(self, *a, **kw):
                raise OSError("cannot spawn")

            BackgroundProcess._start_multiprocessing = _raise_assertion
            BackgroundProcess._start_subprocess = _raise_os_error
            try:
                bg._ensure_started()
            finally:
                BackgroundProcess._start_multiprocessing = original_mp
                BackgroundProcess._start_subprocess = original_sp

            assert bg._disabled is True
            assert not bg.is_running

            # Submit should be a no-op (not raise)
            bg.write_trace(
                request_id="disabled-001",
                workflow_name="test",
                node_name="test",
            )


class TestEngineResilience:
    """Test that Hush engine doesn't crash when background process fails."""

    def test_engine_init_survives_background_failure(self):
        """Test Hush.__init__ completes even if background process fails to start."""
        with GraphNode(name="resilience-test") as graph:
            node = CodeNode(
                name="echo",
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
                code_fn=lambda x: {"result": x},
            )
            START >> node >> END

        # Engine init doesn't touch background — should always succeed
        engine = Hush(graph)
        assert engine.name == "resilience-test"

    @pytest.mark.asyncio
    async def test_engine_runs_without_background(self):
        """Test workflow execution works even when background process is disabled."""
        with GraphNode(name="no-bg-test") as graph:
            node = CodeNode(
                name="double",
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
                code_fn=lambda x: {"result": x * 2},
            )
            START >> node >> END

        # Mock get_background at its source — lazy import in state.py pulls from here
        with patch(
            "hush.core.background.process.get_background",
            side_effect=RuntimeError("background broken"),
        ):
            engine = Hush(graph)

        # Workflow should still execute correctly (no tracer = no background needed)
        result = await engine.run(inputs={"x": 21})
        assert result["result"] == 42

        shutdown_background()


class TestPipeQueue:
    """Test _PipeQueue adapter."""

    def test_pipe_queue_writes_json_lines(self):
        """Test _PipeQueue writes valid JSON lines to pipe."""
        import io

        from hush.core.background.process import _PipeQueue

        buffer = io.BytesIO()
        pq = _PipeQueue(buffer)

        pq.put({"task_type": "trace_write", "data": {"key": "value"}})
        pq.put({"task_type": "shutdown", "data": {}})

        buffer.seek(0)
        lines = buffer.read().decode().strip().split("\n")

        assert len(lines) == 2
        assert json.loads(lines[0]) == {"task_type": "trace_write", "data": {"key": "value"}}
        assert json.loads(lines[1]) == {"task_type": "shutdown", "data": {}}

    def test_pipe_queue_thread_safety(self):
        """Test _PipeQueue handles concurrent writes without corruption."""
        import io
        import threading

        from hush.core.background.process import _PipeQueue

        buffer = io.BytesIO()
        pq = _PipeQueue(buffer)

        errors = []
        num_threads = 10
        writes_per_thread = 100

        def writer(thread_id):
            try:
                for i in range(writes_per_thread):
                    pq.put({"thread": thread_id, "index": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent writes: {errors}"

        # Verify all lines are valid JSON
        buffer.seek(0)
        lines = buffer.read().decode().strip().split("\n")
        assert len(lines) == num_threads * writes_per_thread

        for line in lines:
            parsed = json.loads(line)  # Should not raise
            assert "thread" in parsed
            assert "index" in parsed


class TestBackgroundPerformance:
    """Performance benchmark: measure background tracing overhead on a real workflow.

    Runs a 12-node workflow thousands of times with and without tracing,
    then reports per-request latency overhead.
    """

    @staticmethod
    def _create_pipeline_graph(name: str) -> GraphNode:
        """Create a 12-node pipeline graph for benchmarking."""
        with GraphNode(name=name) as graph:
            prev = None
            for i in range(12):
                node = CodeNode(
                    name=f"step_{i}",
                    code_fn=lambda x, _i=i: {"x": x + _i},
                    inputs={"x": PARENT["x"] if prev is None else prev["x"]},
                    outputs={"x": PARENT} if i == 11 else {},
                )
                if prev is None:
                    START >> node
                else:
                    prev >> node
                if i == 11:
                    node >> END
                prev = node
        return graph

    @pytest.mark.asyncio
    async def test_background_overhead_12_node_pipeline(self):
        """Benchmark: 12-node pipeline, 500 requests, with vs without tracer.

        Measures:
        - Average latency per request (ms)
        - Overhead introduced by background tracing (ms and %)
        - P95 latency
        """
        NUM_REQUESTS = 500
        WARMUP = 10

        # --- Run WITH tracer FIRST (takes the cold-start penalty) ---
        graph_with_tracer = self._create_pipeline_graph("bench-with-tracer")
        engine_with_tracer = Hush(graph_with_tracer)
        tracer = LocalTracer(name="bench")

        # Warmup: process spawn + drain thread + cache warmup
        for i in range(WARMUP):
            await engine_with_tracer.run(
                inputs={"x": 0},
                request_id=f"bench-warmup-yes-{i}",
                tracer=tracer,
            )

        latencies_with_tracer = []
        for i in range(NUM_REQUESTS):
            t0 = time.perf_counter()
            result = await engine_with_tracer.run(
                inputs={"x": 0},
                request_id=f"bench-yes-{i}",
                tracer=tracer,
            )
            latencies_with_tracer.append((time.perf_counter() - t0) * 1000)
            assert result["x"] == 66

        # --- Run WITHOUT tracer SECOND (benefits from warm caches) ---
        graph_no_tracer = self._create_pipeline_graph("bench-no-tracer")
        engine_no_tracer = Hush(graph_no_tracer)

        for i in range(WARMUP):
            await engine_no_tracer.run(inputs={"x": 0}, request_id=f"bench-warmup-no-{i}")

        latencies_no_tracer = []
        for i in range(NUM_REQUESTS):
            t0 = time.perf_counter()
            result = await engine_no_tracer.run(
                inputs={"x": 0},
                request_id=f"bench-no-{i}",
            )
            latencies_no_tracer.append((time.perf_counter() - t0) * 1000)
            # sum(0..11) = 66
            assert result["x"] == 66

        # --- Calculate metrics ---
        def percentile(data, p):
            sorted_data = sorted(data)
            idx = int(len(sorted_data) * p / 100)
            return sorted_data[min(idx, len(sorted_data) - 1)]

        avg_no = sum(latencies_no_tracer) / len(latencies_no_tracer)
        avg_yes = sum(latencies_with_tracer) / len(latencies_with_tracer)
        p95_no = percentile(latencies_no_tracer, 95)
        p95_yes = percentile(latencies_with_tracer, 95)
        overhead_ms = avg_yes - avg_no
        overhead_pct = (overhead_ms / avg_no) * 100 if avg_no > 0 else 0

        print(f"\n{'=' * 60}")
        print(f" Background Tracing Performance Benchmark")
        print(f" {NUM_REQUESTS} requests x 12-node pipeline")
        print(f"{'=' * 60}")
        print(f" {'Metric':<30} {'No Tracer':>12} {'With Tracer':>12}")
        print(f" {'-' * 30} {'-' * 12} {'-' * 12}")
        print(f" {'Avg latency (ms)':<30} {avg_no:>12.3f} {avg_yes:>12.3f}")
        print(f" {'P95 latency (ms)':<30} {p95_no:>12.3f} {p95_yes:>12.3f}")
        print(
            f" {'Min latency (ms)':<30} {min(latencies_no_tracer):>12.3f} {min(latencies_with_tracer):>12.3f}"
        )
        print(
            f" {'Max latency (ms)':<30} {max(latencies_no_tracer):>12.3f} {max(latencies_with_tracer):>12.3f}"
        )
        print(f"{'=' * 60}")
        print(f" Overhead: {overhead_ms:.3f} ms/request ({overhead_pct:.1f}%)")
        print(f"{'=' * 60}")

        # Background tracing overhead should be < 3ms per request for a 12-node graph
        assert overhead_ms < 3.0, (
            f"Background tracing added {overhead_ms:.3f}ms overhead per request, "
            f"expected < 3ms for 12-node pipeline"
        )

        shutdown_background()

    @pytest.mark.asyncio
    async def test_subprocess_fallback_performance(self):
        """Benchmark: subprocess fallback write latency (excluding startup)."""
        WARMUP = 10
        NUM_REQUESTS = 200

        tmpdir = tempfile.mkdtemp(prefix="hush_bench_")
        db_path = Path(tmpdir) / "bench_sub.db"
        bg_sub = BackgroundProcess(db_path)

        _force_subprocess_mode(bg_sub)
        assert bg_sub._is_subprocess is True

        # Warmup writes (includes subprocess spawn cost)
        for i in range(WARMUP):
            bg_sub.write_trace(
                request_id=f"warmup-{i}",
                workflow_name="warmup",
                node_name=f"warmup-{i}",
                execution_order=0,
            )

        # Measured writes
        latencies = []
        for i in range(NUM_REQUESTS):
            t0 = time.perf_counter()
            bg_sub.write_trace(
                request_id=f"bench-sub-{i}",
                workflow_name="bench-subprocess",
                node_name=f"node-{i}",
                execution_order=0,
            )
            latencies.append((time.perf_counter() - t0) * 1000)

        avg_latency = sum(latencies) / len(latencies)
        p95 = sorted(latencies)[int(len(latencies) * 0.95)]

        print(f"\n{'=' * 60}")
        print(f" Subprocess Fallback Write Latency ({NUM_REQUESTS} writes)")
        print(f"{'=' * 60}")
        print(f" Avg: {avg_latency:.3f}ms")
        print(f" P95: {p95:.3f}ms")
        print(f" Max: {max(latencies):.3f}ms")
        print(f"{'=' * 60}")

        bg_sub.shutdown()

        # Subprocess write should be < 2ms per write (post-warmup)
        # Relaxed from 1ms to 2ms for CI runners which are slower
        assert avg_latency < 2.0, (
            f"Subprocess write latency {avg_latency:.3f}ms exceeds 2ms threshold"
        )

        shutdown_background()
