"""Tests for the tracing system (collector, flush_worker, tracers).

Uses CaptureTracer (in-memory) for unit tests.
"""

import threading
import time

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops import FuncOp
from hush.core.tracing import TraceCollector, Tracer
from hush.core.tracing.flush_worker import FlushWorker, _merge_tags


# ---------------------------------------------------------------------------
# CaptureTracer — test helper that stores flush data in memory
# ---------------------------------------------------------------------------
class CaptureTracer(Tracer):
    """Tracer that captures flush data for test assertions."""

    def __init__(self, tags=None):
        super().__init__(tags=tags)
        self.flush_calls = []
        self._flush_event = threading.Event()

    def flush(self, trace_data):
        self.flush_calls.append(trace_data)
        self._flush_event.set()

    def wait_for_flush(self, timeout=5.0):
        """Block until flush() is called (from background thread)."""
        self._flush_event.wait(timeout=timeout)


# ---------------------------------------------------------------------------
# Test: Tracer base class
# ---------------------------------------------------------------------------
class TestTracerBase:
    def test_tracer_creation(self):
        tracer = Tracer(tags=["prod", "ml"])
        assert tracer.tags == ["prod", "ml"]

    def test_tracer_empty_tags(self):
        tracer = Tracer()
        assert tracer.tags == []

    def test_tracer_tags_are_copied(self):
        original = ["a", "b"]
        tracer = Tracer(tags=original)
        returned = tracer.tags
        returned.append("c")
        assert tracer.tags == ["a", "b"]

    def test_tracer_flush_raises(self):
        with pytest.raises(NotImplementedError):
            Tracer().flush({})


# ---------------------------------------------------------------------------
# Test: TraceCollector
# ---------------------------------------------------------------------------
class TestTraceCollector:
    @pytest.mark.asyncio
    async def test_collector_extracts_nodes(self):
        """Collector extracts nodes with op_name and display_name from graph."""
        with GraphOp(name="test-wf") as graph:
            node = FuncOp(
                name="double",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        engine = Hush(graph)
        result = await engine.run(inputs={"x": 5}, request_id="col-001")
        state = result["$state"]

        collector = TraceCollector(graph)
        trace_data = collector.collect(state)

        nodes = {n["op_name"]: n for n in trace_data["nodes"] if n["op_name"]}
        assert "test-wf" in nodes
        assert nodes["test-wf"]["node_type"] == "trace"

        assert "test-wf.double" in nodes
        assert nodes["test-wf.double"]["node_type"] == "span"
        assert nodes["test-wf.double"]["parent_trace_key"] is not None

    @pytest.mark.asyncio
    async def test_collector_extracts_io(self):
        """Collector extracts dynamic data (inputs, outputs, timing) from state."""
        with GraphOp(name="rec-wf") as graph:
            node = FuncOp(
                name="add_ten",
                code_fn=lambda x: {"result": x + 10},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        engine = Hush(graph)
        result = await engine.run(inputs={"x": 5}, request_id="col-002")
        state = result["$state"]

        collector = TraceCollector(graph)
        trace_data = collector.collect(state)

        assert trace_data["request_id"] == "col-002"
        assert trace_data["workflow_name"] == "rec-wf"

        nodes = {n["op_name"]: n for n in trace_data["nodes"] if n["op_name"]}
        assert "rec-wf.add_ten" in nodes

        rec = nodes["rec-wf.add_ten"]
        assert rec["inputs"]["x"] == 5
        assert rec["outputs"]["result"] == 15
        assert rec["duration_ms"] is not None
        assert rec["duration_ms"] > 0
        assert rec["start_time"] is not None
        assert rec["end_time"] is not None

    @pytest.mark.asyncio
    async def test_collector_preserves_execution_order(self):
        """Nodes are in topological order (step_a before step_b)."""
        with GraphOp(name="order-wf") as graph:
            a = FuncOp(
                name="step_a",
                code_fn=lambda x: {"mid": x + 1},
                inputs={"x": PARENT["x"]},
            )
            b = FuncOp(
                name="step_b",
                code_fn=lambda mid: {"result": mid * 2},
                inputs={"mid": a["mid"]},
                outputs={"result": PARENT},
            )
            START >> a >> b >> END

        engine = Hush(graph)
        result = await engine.run(inputs={"x": 0}, request_id="col-003")
        state = result["$state"]

        collector = TraceCollector(graph)
        trace_data = collector.collect(state)

        op_names = [n["op_name"] for n in trace_data["nodes"] if n["op_name"]]
        assert "order-wf.step_a" in op_names
        assert "order-wf.step_b" in op_names
        idx_a = op_names.index("order-wf.step_a")
        idx_b = op_names.index("order-wf.step_b")
        assert idx_a < idx_b, "step_a should appear before step_b"

    @pytest.mark.asyncio
    async def test_collector_with_dynamic_tags(self):
        """Dynamic tags from $tags are captured in trace_data.tags."""

        def tagged_fn(x):
            return {"result": x, "$tags": ["dynamic-one", "dynamic-two"]}

        with GraphOp(name="tag-wf") as graph:
            node = FuncOp(
                name="tagger",
                code_fn=tagged_fn,
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        engine = Hush(graph)
        result = await engine.run(inputs={"x": 42}, request_id="col-004")
        state = result["$state"]

        collector = TraceCollector(graph)
        trace_data = collector.collect(state)

        assert "dynamic-one" in trace_data["tags"]
        assert "dynamic-two" in trace_data["tags"]

    @pytest.mark.asyncio
    async def test_collector_metadata_fields(self):
        """Collector populates user_id, session_id, request_id."""
        with GraphOp(name="meta-wf") as graph:
            node = FuncOp(
                name="noop",
                code_fn=lambda x: {"result": x},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        engine = Hush(graph)
        result = await engine.run(
            inputs={"x": 1},
            request_id="req-abc",
            user_id="user-123",
            session_id="sess-456",
        )
        state = result["$state"]

        collector = TraceCollector(graph)
        trace_data = collector.collect(state)

        assert trace_data["request_id"] == "req-abc"
        assert trace_data["user_id"] == "user-123"
        assert trace_data["session_id"] == "sess-456"


# ---------------------------------------------------------------------------
# Test: FlushWorker + tag merging
# ---------------------------------------------------------------------------
class TestFlushWorker:
    def test_merge_tags_static_first(self):
        merged = _merge_tags(["dynamic"], ["static"])
        assert merged == ["static", "dynamic"]

    def test_merge_tags_dedup(self):
        merged = _merge_tags(["shared", "dynamic"], ["shared", "static"])
        assert merged == ["shared", "static", "dynamic"]
        assert merged.count("shared") == 1

    def test_merge_tags_empty(self):
        assert _merge_tags([], []) == []
        assert _merge_tags(["a"], []) == ["a"]
        assert _merge_tags([], ["b"]) == ["b"]

    @pytest.mark.asyncio
    async def test_flush_worker_submits_to_tracer(self):
        """FlushWorker runs collect + flush in background thread."""
        with GraphOp(name="fw-wf") as graph:
            node = FuncOp(
                name="double",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        engine = Hush(graph)
        result = await engine.run(inputs={"x": 7}, request_id="fw-001")
        state = result["$state"]

        tracer = CaptureTracer(tags=["static-tag"])
        worker = FlushWorker(max_workers=1)
        try:
            worker.submit([tracer], TraceCollector(graph), state)
            tracer.wait_for_flush(timeout=5)

            assert len(tracer.flush_calls) == 1
            data = tracer.flush_calls[0]
            assert data["request_id"] == "fw-001"
            assert data["workflow_name"] == "fw-wf"
            # Static tags should be merged
            assert "static-tag" in data["tags"]
        finally:
            worker.shutdown()

    @pytest.mark.asyncio
    async def test_flush_worker_merges_dynamic_and_static_tags(self):
        """FlushWorker merges dynamic ($tags) + static (tracer) tags."""

        def tagged_fn(x):
            return {"result": x, "$tags": ["dynamic-tag"]}

        with GraphOp(name="merge-wf") as graph:
            node = FuncOp(
                name="proc",
                code_fn=tagged_fn,
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        engine = Hush(graph)
        result = await engine.run(inputs={"x": 1}, request_id="fw-002")
        state = result["$state"]

        tracer = CaptureTracer(tags=["static-tag"])
        worker = FlushWorker(max_workers=1)
        try:
            worker.submit([tracer], TraceCollector(graph), state)
            tracer.wait_for_flush(timeout=5)

            tags = tracer.flush_calls[0]["tags"]
            assert "static-tag" in tags
            assert "dynamic-tag" in tags
            # Static first, then dynamic
            assert tags.index("static-tag") < tags.index("dynamic-tag")
        finally:
            worker.shutdown()

    @pytest.mark.asyncio
    async def test_flush_worker_multiple_tracers(self):
        """FlushWorker flushes to all tracers in the list."""
        with GraphOp(name="multi-wf") as graph:
            node = FuncOp(
                name="noop",
                code_fn=lambda x: {"result": x},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        engine = Hush(graph)
        result = await engine.run(inputs={"x": 1}, request_id="fw-003")
        state = result["$state"]

        t1 = CaptureTracer(tags=["tracer-1"])
        t2 = CaptureTracer(tags=["tracer-2"])

        worker = FlushWorker(max_workers=1)
        try:
            worker.submit([t1, t2], TraceCollector(graph), state)
            t1.wait_for_flush(timeout=5)
            t2.wait_for_flush(timeout=5)

            assert len(t1.flush_calls) == 1
            assert len(t2.flush_calls) == 1
            assert "tracer-1" in t1.flush_calls[0]["tags"]
            assert "tracer-2" in t2.flush_calls[0]["tags"]
        finally:
            worker.shutdown()


# ---------------------------------------------------------------------------
# Test: Engine integration with tracer= parameter
# ---------------------------------------------------------------------------
class TestEngineWithTracers:
    @pytest.mark.asyncio
    async def test_engine_runs_with_tracers(self):
        """Engine accepts tracer= and workflow result is correct."""
        with GraphOp(name="engine-wf") as graph:
            node = FuncOp(
                name="add",
                code_fn=lambda x: {"result": x + 10},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        tracer = CaptureTracer()
        engine = Hush(graph)

        result = await engine.run(
            inputs={"x": 5},
            request_id="eng-001",
            tracer=tracer,
        )

        # Workflow result is correct
        assert result["result"] == 15

        # Wait for background flush
        tracer.wait_for_flush(timeout=5)

        # Trace data was captured
        assert len(tracer.flush_calls) == 1
        data = tracer.flush_calls[0]
        assert data["request_id"] == "eng-001"
        assert data["workflow_name"] == "engine-wf"
        assert len(data["nodes"]) >= 2  # graph + node

    @pytest.mark.asyncio
    async def test_engine_multiple_runs(self):
        """Multiple engine.run() calls each produce separate trace data."""
        with GraphOp(name="multi-run-wf") as graph:
            node = FuncOp(
                name="double",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        tracer = CaptureTracer()
        engine = Hush(graph)

        for i in range(3):
            result = await engine.run(
                inputs={"x": i},
                request_id=f"multi-{i}",
                tracer=tracer,
            )
            assert result["result"] == i * 2

        # Wait for all flushes
        time.sleep(1.0)

        assert len(tracer.flush_calls) == 3
        req_ids = {d["request_id"] for d in tracer.flush_calls}
        assert req_ids == {"multi-0", "multi-1", "multi-2"}

    @pytest.mark.asyncio
    async def test_engine_with_user_session_ids(self):
        """Engine passes user_id, session_id through to trace data."""
        with GraphOp(name="ids-wf") as graph:
            node = FuncOp(
                name="echo",
                code_fn=lambda data: {"out": data},
                inputs={"data": PARENT["data"]},
                outputs={"out": PARENT},
            )
            START >> node >> END

        tracer = CaptureTracer()
        engine = Hush(graph)

        await engine.run(
            inputs={"data": "hello"},
            request_id="ids-001",
            user_id="user-abc",
            session_id="sess-xyz",
            tracer=tracer,
        )

        tracer.wait_for_flush(timeout=5)

        data = tracer.flush_calls[0]
        assert data["user_id"] == "user-abc"
        assert data["session_id"] == "sess-xyz"

    @pytest.mark.asyncio
    async def test_engine_without_tracers_still_works(self):
        """Engine works normally when no tracers are provided."""
        with GraphOp(name="no-trace-wf") as graph:
            node = FuncOp(
                name="add",
                code_fn=lambda x: {"result": x + 1},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        engine = Hush(graph)
        result = await engine.run(inputs={"x": 99})
        assert result["result"] == 100


# ---------------------------------------------------------------------------
# Test: Tags end-to-end
# ---------------------------------------------------------------------------
class TestTracerTags:
    @pytest.mark.asyncio
    async def test_static_tags_only(self):
        """Static tags from tracer appear in flushed data."""
        with GraphOp(name="static-wf") as graph:
            node = FuncOp(
                name="noop",
                code_fn=lambda x: {"result": x},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        tracer = CaptureTracer(tags=["prod", "critical"])
        engine = Hush(graph)

        await engine.run(inputs={"x": 1}, tracer=tracer)
        tracer.wait_for_flush(timeout=5)

        tags = tracer.flush_calls[0]["tags"]
        assert "prod" in tags
        assert "critical" in tags

    @pytest.mark.asyncio
    async def test_dynamic_tags_only(self):
        """Dynamic tags from $tags in op output appear in flushed data."""

        def process_with_tags(x):
            result = x * 2
            tags = ["computed"]
            if result > 10:
                tags.append("high-value")
            return {"result": result, "$tags": tags}

        with GraphOp(name="dynamic-wf") as graph:
            node = FuncOp(
                name="proc",
                code_fn=process_with_tags,
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        tracer = CaptureTracer()
        engine = Hush(graph)

        await engine.run(inputs={"x": 10}, tracer=tracer)
        tracer.wait_for_flush(timeout=5)

        tags = tracer.flush_calls[0]["tags"]
        assert "computed" in tags
        assert "high-value" in tags

    @pytest.mark.asyncio
    async def test_merged_tags(self):
        """Static + dynamic tags are merged, static first."""

        def tagged_fn(x):
            return {"result": x, "$tags": ["dynamic-tag", "runtime"]}

        with GraphOp(name="merged-wf") as graph:
            node = FuncOp(
                name="proc",
                code_fn=tagged_fn,
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        tracer = CaptureTracer(tags=["static-tag", "env:test"])
        engine = Hush(graph)

        await engine.run(inputs={"x": 1}, tracer=tracer)
        tracer.wait_for_flush(timeout=5)

        tags = tracer.flush_calls[0]["tags"]
        assert "static-tag" in tags
        assert "env:test" in tags
        assert "dynamic-tag" in tags
        assert "runtime" in tags

    @pytest.mark.asyncio
    async def test_duplicate_tags_deduplicated(self):
        """Duplicate tags between static and dynamic are deduplicated."""

        def tagged_fn(x):
            return {"result": x, "$tags": ["shared-tag", "unique-dynamic"]}

        with GraphOp(name="dedup-wf") as graph:
            node = FuncOp(
                name="proc",
                code_fn=tagged_fn,
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        tracer = CaptureTracer(tags=["shared-tag", "unique-static"])
        engine = Hush(graph)

        await engine.run(inputs={"x": 1}, tracer=tracer)
        tracer.wait_for_flush(timeout=5)

        tags = tracer.flush_calls[0]["tags"]
        assert tags.count("shared-tag") == 1
        assert "unique-static" in tags
        assert "unique-dynamic" in tags


# ---------------------------------------------------------------------------
# Test: Non-blocking behavior
# ---------------------------------------------------------------------------
class TestTracerNonBlocking:
    @pytest.mark.asyncio
    async def test_tracers_do_not_block_workflow(self):
        """Verify tracer= path doesn't add significant latency."""
        NUM_REQUESTS = 50

        def create_graph():
            with GraphOp(name="stress-test") as graph:
                node = FuncOp(
                    name="double",
                    code_fn=lambda x: {"result": x * 2},
                    inputs={"x": PARENT["x"]},
                    outputs={"result": PARENT},
                )
                START >> node >> END
            return graph

        # Without tracers
        g1 = create_graph()
        e1 = Hush(g1)
        t0 = time.perf_counter()
        for i in range(NUM_REQUESTS):
            r = await e1.run(inputs={"x": i}, request_id=f"no-{i}")
            assert r["result"] == i * 2
        time_without = time.perf_counter() - t0

        # With tracers
        g2 = create_graph()
        e2 = Hush(g2)
        tracer = CaptureTracer()
        t0 = time.perf_counter()
        for i in range(NUM_REQUESTS):
            r = await e2.run(inputs={"x": i}, request_id=f"yes-{i}", tracer=tracer)
            assert r["result"] == i * 2
        time_with = time.perf_counter() - t0

        avg_no = (time_without / NUM_REQUESTS) * 1000
        avg_yes = (time_with / NUM_REQUESTS) * 1000
        overhead = avg_yes - avg_no

        print(f"\n  Without tracers: {avg_no:.3f}ms/req")
        print(f"  With tracers:    {avg_yes:.3f}ms/req")
        print(f"  Overhead:        {overhead:.3f}ms/req")

        assert overhead < 5, f"Overhead {overhead:.3f}ms exceeds 5ms threshold"


# ---------------------------------------------------------------------------
# Test: Iteration nodes
# ---------------------------------------------------------------------------
class TestTracerWithIterationNodes:
    @pytest.mark.asyncio
    async def test_tracer_with_generator_iteration(self):
        """Tracers work with generator-based iteration (replaces ForOp)."""
        from hush.core.ops.transform.func_op import op

        @op
        def each_item(items: list):
            for item in items:
                yield {"value": item}

        @op
        def double(value: int):
            return {"result": value * 2}

        with GraphOp(name="loop-wf") as outer:
            src = each_item(items=PARENT["items"])
            node = double(value=src["value"])
            START >> src >> node >> END

        tracer = CaptureTracer()
        engine = Hush(outer)
        await engine.run(inputs={"items": [1, 2, 3]}, tracer=tracer)

        tracer.wait_for_flush(timeout=5)

        data = tracer.flush_calls[0]
        # Should have nodes for graph and ops
        assert len(data["nodes"]) >= 2
