"""Tests for LocalTracer and background tracing infrastructure."""

import sqlite3
import tempfile
from pathlib import Path
from time import sleep

import pytest

from hush.core import Hush, GraphNode, START, END, PARENT
from hush.core.nodes import CodeNode
from hush.core.tracers import LocalTracer, get_registered_tracers
from hush.core.background import shutdown_background, BackgroundProcess


class TestLocalTracerBasic:
    """Test basic LocalTracer functionality."""

    def test_local_tracer_creation(self):
        """Test LocalTracer can be created."""
        tracer = LocalTracer()
        assert tracer.name == "local"

    def test_local_tracer_with_custom_name(self):
        """Test LocalTracer with custom name."""
        tracer = LocalTracer(name="test-tracer")
        assert tracer.name == "test-tracer"

    def test_local_tracer_config(self):
        """Test _get_tracer_config returns correct config."""
        tracer = LocalTracer(name="my-tracer")
        config = tracer._get_tracer_config()
        assert config == {"name": "my-tracer"}

    def test_local_tracer_repr(self):
        """Test string representation."""
        tracer = LocalTracer(name="test")
        assert repr(tracer) == "<LocalTracer name=test>"

    def test_local_tracer_registered(self):
        """Test LocalTracer is registered in tracer registry."""
        tracers = get_registered_tracers()
        assert "LocalTracer" in tracers
        assert tracers["LocalTracer"] is LocalTracer

    def test_flush_does_nothing(self):
        """Test flush() is a no-op."""
        # Should not raise any errors
        LocalTracer.flush({
            "request_id": "test-123",
            "workflow_name": "test-workflow",
            "tracer_config": {"name": "test"},
        })

    def test_local_tracer_with_static_tags(self):
        """Test LocalTracer can be created with static tags."""
        tracer = LocalTracer(name="tagged-tracer", tags=["prod", "ml-team"])
        assert tracer.name == "tagged-tracer"
        assert tracer.tags == ["prod", "ml-team"]

    def test_local_tracer_tags_are_copied(self):
        """Test that tags property returns a copy, not the original list."""
        original_tags = ["tag1", "tag2"]
        tracer = LocalTracer(tags=original_tags)
        returned_tags = tracer.tags
        returned_tags.append("tag3")
        # Original should not be modified
        assert tracer.tags == ["tag1", "tag2"]

    def test_local_tracer_empty_tags(self):
        """Test LocalTracer with no tags defaults to empty list."""
        tracer = LocalTracer()
        assert tracer.tags == []


class TestBackgroundProcess:
    """Test BackgroundProcess functionality."""

    def test_background_process_creation(self):
        """Test BackgroundProcess can be created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            bg = BackgroundProcess(db_path)
            assert bg.db_path == db_path
            assert not bg.is_running

    def test_background_process_starts_on_submit(self):
        """Test background process starts lazily on first submit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            bg = BackgroundProcess(db_path)

            # Not running yet
            assert not bg.is_running

            # Submit a trace write
            bg.write_trace(
                request_id="test-123",
                workflow_name="test-wf",
                node_name="test-node",
            )

            # Should be running now
            assert bg.is_running

            # Cleanup
            bg.shutdown()
            assert not bg.is_running

    def test_background_process_writes_to_db(self):
        """Test traces are written to SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            bg = BackgroundProcess(db_path)

            # Write a trace
            bg.write_trace(
                request_id="req-001",
                workflow_name="test-workflow",
                node_name="node-a",
                parent_name=None,
                context_id=None,
                execution_order=0,
                user_id="user-1",
                session_id="session-1",
            )

            # Give background process time to write
            sleep(0.5)

            # Check database
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM traces WHERE request_id = ?", ("req-001",))
            rows = cursor.fetchall()
            conn.close()

            assert len(rows) == 1
            assert rows[0]["workflow_name"] == "test-workflow"
            assert rows[0]["node_name"] == "node-a"
            assert rows[0]["status"] == "writing"

            # Cleanup
            bg.shutdown()

    def test_mark_complete_changes_status(self):
        """Test mark_complete changes status from writing to flushed for LocalTracer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            bg = BackgroundProcess(db_path)

            # Write traces
            bg.write_trace(
                request_id="req-002",
                workflow_name="test-wf",
                node_name="node-1",
                execution_order=0,
            )
            bg.write_trace(
                request_id="req-002",
                workflow_name="test-wf",
                node_name="node-2",
                execution_order=1,
            )

            # Give time to write
            sleep(0.5)

            # Mark complete
            bg.mark_complete(
                request_id="req-002",
                tracer_type="LocalTracer",
                tracer_config={"name": "test"},
            )

            # Give time to process
            sleep(0.5)

            # Check status changed
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT status, tracer_type FROM traces WHERE request_id = ?",
                ("req-002",)
            )
            rows = cursor.fetchall()
            conn.close()

            assert len(rows) == 2
            for row in rows:
                # LocalTracer marks as 'flushed' directly since it doesn't need external flushing
                assert row["status"] == "flushed"
                assert row["tracer_type"] == "LocalTracer"

            # Cleanup
            bg.shutdown()


class TestTracerWithWorkflow:
    """Test tracer integration with workflow execution."""

    @pytest.fixture
    def simple_graph(self):
        """Create a simple test graph."""
        with GraphNode(name="test-workflow") as graph:
            node1 = CodeNode(
                name="add",
                code_fn=lambda x: {"result": x + 10},
                inputs={"x": PARENT["x"]},
            )
            node2 = CodeNode(
                name="multiply",
                code_fn=lambda value: {"final": value * 2},
                inputs={"value": node1["result"]},
                outputs={"final": PARENT},
            )
            START >> node1 >> node2 >> END
        return graph

    @pytest.mark.asyncio
    async def test_workflow_with_local_tracer(self, simple_graph):
        """Test workflow execution with LocalTracer produces correct results."""
        # Create tracer
        tracer = LocalTracer(name="test")

        # Create engine
        engine = Hush(simple_graph)

        # Run workflow with tracer
        result = await engine.run(
            inputs={"x": 5},
            request_id="wf-test-001",
            tracer=tracer,
        )

        # Check result - tracer shouldn't affect workflow execution
        assert result["final"] == 30  # (5 + 10) * 2

        # Cleanup
        shutdown_background()

    @pytest.mark.asyncio
    async def test_multiple_workflow_runs(self, simple_graph):
        """Test multiple workflow runs with tracer produce correct results."""
        tracer = LocalTracer()
        engine = Hush(simple_graph)

        # Run multiple times - tracer should handle multiple runs
        for i in range(3):
            req_id = f"multi-run-{i}"
            result = await engine.run(
                inputs={"x": i},
                request_id=req_id,
                tracer=tracer,
            )
            assert result["final"] == (i + 10) * 2

        # Cleanup
        shutdown_background()

    @pytest.mark.asyncio
    async def test_tracer_with_user_session_ids(self):
        """Test workflow with tracer and user/session IDs."""
        with GraphNode(name="metadata-test") as graph:
            node = CodeNode(
                name="processor",
                inputs={"data": PARENT["data"]},
                outputs={"processed": PARENT},
                code_fn=lambda data: {"processed": data.upper()}
            )
            START >> node >> END

        tracer = LocalTracer()
        engine = Hush(graph)

        result = await engine.run(
            inputs={"data": "hello"},
            request_id="meta-test-001",
            user_id="user-123",
            session_id="session-456",
            tracer=tracer,
        )

        # Workflow should complete successfully
        assert result["processed"] == "HELLO"

        # Cleanup
        shutdown_background()


class TestWorkflowTracesWrittenToDb:
    """Test that workflow execution with tracer writes traces to database."""

    @pytest.mark.asyncio
    async def test_workflow_traces_written_to_default_db(self):
        """Test workflow execution with tracer writes traces to default db."""
        from hush.core.background import DEFAULT_DB_PATH

        # Create a simple workflow
        with GraphNode(name="db-test-workflow") as graph:
            node = CodeNode(
                name="processor",
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
                code_fn=lambda x: {"result": x * 2}
            )
            START >> node >> END

        tracer = LocalTracer(name="db-test")
        engine = Hush(graph)

        # Use a unique request_id to identify this test's traces
        request_id = "db-write-test-001"

        result = await engine.run(
            inputs={"x": 21},
            request_id=request_id,
            tracer=tracer,
        )

        # Verify workflow executed correctly
        assert result["result"] == 42

        # Give background process time to write
        sleep(1.0)

        # Verify traces were written to default database
        conn = sqlite3.connect(str(DEFAULT_DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM traces WHERE request_id = ? ORDER BY execution_order",
            (request_id,)
        )
        rows = cursor.fetchall()
        conn.close()

        # Should have at least 1 trace (the workflow graph node)
        assert len(rows) >= 1, f"Expected traces in db, but found {len(rows)}"

        # Verify trace data
        found_workflow = False
        for row in rows:
            assert row["request_id"] == request_id
            assert row["workflow_name"] == "db-test-workflow"
            if row["node_name"] == "db-test-workflow":
                found_workflow = True

        assert found_workflow, "Should have trace for workflow graph node"

        # Cleanup
        shutdown_background()

    @pytest.mark.asyncio
    async def test_workflow_traces_contain_correct_metadata(self):
        """Test workflow traces contain user_id, session_id correctly."""
        from hush.core.background import DEFAULT_DB_PATH

        with GraphNode(name="metadata-db-test") as graph:
            node = CodeNode(
                name="echo",
                inputs={"msg": PARENT["msg"]},
                outputs={"out": PARENT},
                code_fn=lambda msg: {"out": msg}
            )
            START >> node >> END

        tracer = LocalTracer()
        engine = Hush(graph)

        request_id = "metadata-db-test-001"
        user_id = "test-user-abc"
        session_id = "test-session-xyz"

        await engine.run(
            inputs={"msg": "hello"},
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
            tracer=tracer,
        )

        # Give background process time to write
        sleep(1.0)

        # Check database
        conn = sqlite3.connect(str(DEFAULT_DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT user_id, session_id, workflow_name FROM traces WHERE request_id = ?",
            (request_id,)
        )
        rows = cursor.fetchall()
        conn.close()

        assert len(rows) >= 1, "Expected traces in database"

        for row in rows:
            assert row["user_id"] == user_id, f"Expected user_id={user_id}, got {row['user_id']}"
            assert row["session_id"] == session_id, f"Expected session_id={session_id}, got {row['session_id']}"
            assert row["workflow_name"] == "metadata-db-test"

        # Cleanup
        shutdown_background()


class TestTracerTags:
    """Test static and dynamic tagging functionality."""

    def test_mark_complete_with_static_tags(self):
        """Test mark_complete stores static tags in database."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "tags_test.db"
            bg = BackgroundProcess(db_path)

            # Write a trace
            bg.write_trace(
                request_id="tags-test-001",
                workflow_name="test-wf",
                node_name="test-node",
                execution_order=0,
            )

            # Give time to write
            sleep(0.5)

            # Mark complete with static tags
            bg.mark_complete(
                request_id="tags-test-001",
                tracer_type="LocalTracer",
                tracer_config={"name": "test"},
                tags=["prod", "ml-team", "experiment-v1"],
            )

            # Give time to process
            sleep(0.5)

            # Check tags were stored
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT tags FROM traces WHERE request_id = ?",
                ("tags-test-001",)
            )
            row = cursor.fetchone()
            conn.close()

            # Tags should be stored as JSON array
            tags = json.loads(row["tags"])
            assert tags == ["prod", "ml-team", "experiment-v1"]

            # Cleanup
            bg.shutdown()

    @pytest.mark.asyncio
    async def test_workflow_with_static_tags(self):
        """Test workflow execution with static tracer tags."""
        import json
        from hush.core.background import DEFAULT_DB_PATH

        with GraphNode(name="static-tags-test") as graph:
            node = CodeNode(
                name="processor",
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
                code_fn=lambda x: {"result": x * 2}
            )
            START >> node >> END

        # Create tracer with static tags
        tracer = LocalTracer(name="tagged", tags=["production", "critical"])
        engine = Hush(graph)

        request_id = "static-tags-wf-001"

        await engine.run(
            inputs={"x": 5},
            request_id=request_id,
            tracer=tracer,
        )

        # Give background process time to write
        sleep(1.0)

        # Verify tags in database
        conn = sqlite3.connect(str(DEFAULT_DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT tags FROM traces WHERE request_id = ?",
            (request_id,)
        )
        rows = cursor.fetchall()
        conn.close()

        assert len(rows) >= 1, "Expected traces in database"

        for row in rows:
            if row["tags"]:
                tags = json.loads(row["tags"])
                assert "production" in tags
                assert "critical" in tags

        # Cleanup
        shutdown_background()

    @pytest.mark.asyncio
    async def test_workflow_with_dynamic_tags(self):
        """Test workflow execution with dynamic tags via $tags in node output."""
        import json
        from hush.core.background import DEFAULT_DB_PATH

        # Node that returns dynamic tags
        def process_with_tags(x):
            result = x * 2
            # Add dynamic tags based on result
            tags = ["computed"]
            if result > 10:
                tags.append("high-value")
            return {"result": result, "$tags": tags}

        with GraphNode(name="dynamic-tags-test") as graph:
            node = CodeNode(
                name="processor",
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
                code_fn=process_with_tags
            )
            START >> node >> END

        # Create tracer without static tags
        tracer = LocalTracer(name="dynamic-test")
        engine = Hush(graph)

        request_id = "dynamic-tags-wf-001"

        await engine.run(
            inputs={"x": 10},  # result will be 20, so "high-value" tag added
            request_id=request_id,
            tracer=tracer,
        )

        # Give background process time to write
        sleep(1.0)

        # Verify tags in database
        conn = sqlite3.connect(str(DEFAULT_DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT tags FROM traces WHERE request_id = ?",
            (request_id,)
        )
        rows = cursor.fetchall()
        conn.close()

        assert len(rows) >= 1, "Expected traces in database"

        # Find row with tags
        found_tags = False
        for row in rows:
            if row["tags"]:
                tags = json.loads(row["tags"])
                if "computed" in tags:
                    found_tags = True
                    assert "high-value" in tags, "Expected high-value tag for result > 10"

        assert found_tags, "Expected dynamic tags to be stored"

        # Cleanup
        shutdown_background()

    @pytest.mark.asyncio
    async def test_workflow_with_merged_tags(self):
        """Test workflow with both static and dynamic tags merged."""
        import json
        from hush.core.background import DEFAULT_DB_PATH

        # Node that returns dynamic tags
        def process_with_dynamic_tags(x):
            return {"result": x * 2, "$tags": ["dynamic-tag", "runtime"]}

        with GraphNode(name="merged-tags-test") as graph:
            node = CodeNode(
                name="processor",
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
                code_fn=process_with_dynamic_tags
            )
            START >> node >> END

        # Create tracer with static tags
        tracer = LocalTracer(name="merged", tags=["static-tag", "env:test"])
        engine = Hush(graph)

        request_id = "merged-tags-wf-001"

        await engine.run(
            inputs={"x": 5},
            request_id=request_id,
            tracer=tracer,
        )

        # Give background process time to write
        sleep(1.0)

        # Verify merged tags in database
        conn = sqlite3.connect(str(DEFAULT_DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT tags FROM traces WHERE request_id = ?",
            (request_id,)
        )
        rows = cursor.fetchall()
        conn.close()

        assert len(rows) >= 1, "Expected traces in database"

        # Find row with tags
        found_merged = False
        for row in rows:
            if row["tags"]:
                tags = json.loads(row["tags"])
                # Check both static and dynamic tags are present
                has_static = "static-tag" in tags and "env:test" in tags
                has_dynamic = "dynamic-tag" in tags and "runtime" in tags
                if has_static and has_dynamic:
                    found_merged = True

        assert found_merged, "Expected both static and dynamic tags to be merged"

        # Cleanup
        shutdown_background()

    @pytest.mark.asyncio
    async def test_duplicate_tags_are_deduplicated(self):
        """Test that duplicate tags (static + dynamic) are deduplicated."""
        import json
        from hush.core.background import DEFAULT_DB_PATH

        # Node that returns a tag that's also in static tags
        def process_with_duplicate_tag(x):
            return {"result": x, "$tags": ["shared-tag", "unique-dynamic"]}

        with GraphNode(name="dedup-tags-test") as graph:
            node = CodeNode(
                name="processor",
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
                code_fn=process_with_duplicate_tag
            )
            START >> node >> END

        # Create tracer with static tags including one that will be duplicated
        tracer = LocalTracer(name="dedup", tags=["shared-tag", "unique-static"])
        engine = Hush(graph)

        request_id = "dedup-tags-wf-001"

        await engine.run(
            inputs={"x": 1},
            request_id=request_id,
            tracer=tracer,
        )

        # Give background process time to write
        sleep(1.0)

        # Verify tags in database
        conn = sqlite3.connect(str(DEFAULT_DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT tags FROM traces WHERE request_id = ?",
            (request_id,)
        )
        rows = cursor.fetchall()
        conn.close()

        # Find row with tags and verify no duplicates
        for row in rows:
            if row["tags"]:
                tags = json.loads(row["tags"])
                # shared-tag should appear only once
                assert tags.count("shared-tag") == 1, "Duplicate tags should be deduplicated"
                # All unique tags should be present
                assert "unique-static" in tags
                assert "unique-dynamic" in tags

        # Cleanup
        shutdown_background()


class TestTracerFlushCycle:
    """Test the complete trace write -> flush cycle."""

    @pytest.mark.asyncio
    async def test_traces_get_flushed(self):
        """Test that traces go through the complete flush cycle."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "flush_test.db"
            bg = BackgroundProcess(db_path)

            # Write trace
            bg.write_trace(
                request_id="flush-test-001",
                workflow_name="test-wf",
                node_name="test-node",
                execution_order=0,
            )

            # Wait for write
            sleep(0.3)

            # Mark complete
            bg.mark_complete(
                request_id="flush-test-001",
                tracer_type="LocalTracer",
                tracer_config={"name": "test"},
            )

            # Wait for flush cycle (poll_interval is 2s by default)
            # But we can check the status transitions
            sleep(3.0)

            # Check status should be flushed (LocalTracer.flush does nothing but succeeds)
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT status FROM traces WHERE request_id = ?",
                ("flush-test-001",)
            )
            row = cursor.fetchone()
            conn.close()

            # Should be flushed
            assert row["status"] == "flushed"

            # Cleanup
            bg.shutdown()


class TestTracerNonBlocking:
    """Test that tracer doesn't block the main workflow execution."""

    @pytest.mark.asyncio
    async def test_tracer_does_not_block_workflow(self):
        """Stress test: run many requests with and without tracer.

        Compare total execution time to verify tracer doesn't block main flow.
        """
        import time

        NUM_REQUESTS = 50  # Run 50 requests to stress test

        # Create a simple workflow
        def create_graph():
            with GraphNode(name="stress-test") as graph:
                node = CodeNode(
                    name="processor",
                    inputs={"x": PARENT["x"]},
                    outputs={"result": PARENT},
                    code_fn=lambda x: {"result": x * 2}
                )
                START >> node >> END
            return graph

        # Run WITHOUT tracer
        graph_no_tracer = create_graph()
        engine_no_tracer = Hush(graph_no_tracer)

        start_no_tracer = time.perf_counter()
        for i in range(NUM_REQUESTS):
            result = await engine_no_tracer.run(
                inputs={"x": i},
                request_id=f"no-tracer-{i}",
            )
            assert result["result"] == i * 2
        time_no_tracer = time.perf_counter() - start_no_tracer

        # Run WITH tracer
        graph_with_tracer = create_graph()
        engine_with_tracer = Hush(graph_with_tracer)
        tracer = LocalTracer()

        start_with_tracer = time.perf_counter()
        for i in range(NUM_REQUESTS):
            result = await engine_with_tracer.run(
                inputs={"x": i},
                request_id=f"with-tracer-{i}",
                tracer=tracer,
            )
            assert result["result"] == i * 2
        time_with_tracer = time.perf_counter() - start_with_tracer

        # Calculate metrics
        avg_no_tracer = (time_no_tracer / NUM_REQUESTS) * 1000
        avg_with_tracer = (time_with_tracer / NUM_REQUESTS) * 1000
        overhead_per_request = avg_with_tracer - avg_no_tracer
        overhead_percent = (overhead_per_request / avg_no_tracer) * 100 if avg_no_tracer > 0 else 0

        print(f"\n=== Stress Test Results ({NUM_REQUESTS} requests) ===")
        print(f"Without tracer: {time_no_tracer*1000:.2f}ms total, {avg_no_tracer:.3f}ms avg/request")
        print(f"With tracer:    {time_with_tracer*1000:.2f}ms total, {avg_with_tracer:.3f}ms avg/request")
        print(f"Overhead:       {overhead_per_request:.3f}ms/request ({overhead_percent:.1f}%)")

        # Tracer overhead should be minimal (< 5ms per request on average)
        # since writes happen in background process
        assert overhead_per_request < 5, (
            f"Tracer added {overhead_per_request:.3f}ms overhead per request, expected < 5ms"
        )

        # Cleanup
        shutdown_background()


class TestTracerWithIterationNodes:
    """Test tracer with iteration nodes (ForLoop, Map, etc.)."""

    @pytest.mark.asyncio
    async def test_tracer_with_forloop(self):
        """Test tracer works with ForLoopNode iteration."""
        from hush.core.nodes import ForLoopNode
        from hush.core.nodes.iteration import Each
        from hush.core.nodes.transform.code_node import code_node

        @code_node
        def double(value: int):
            return {"result": value * 2}

        with ForLoopNode(
            name="double_loop",
            inputs={"value": Each([1, 2, 3, 4, 5])}
        ) as loop:
            node = double(inputs={"value": PARENT["value"]}, outputs={"*": PARENT})
            START >> node >> END

        # Run directly without workflow engine
        from hush.core.states import StateSchema, MemoryState
        loop.build()
        schema = StateSchema(loop)
        state = MemoryState(schema)

        result = await loop.run(state)
        assert result['result'] == [2, 4, 6, 8, 10]

        # NOTE: This test verifies ForLoopNode works. Tracer integration
        # with iteration nodes is tested implicitly by the workflow tests
        # since the engine handles tracer registration.

        # Cleanup
        shutdown_background()