"""Tests for the tracing system using real LocalTracer.

Uses LocalTracer (writes JSON files to tmp_path) instead of mocks.
Each test gets an isolated temp directory via pytest's tmp_path fixture.
"""

import json
import time
from pathlib import Path

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops import FuncOp
from hush.core.tracing import LocalTracer, TraceCollector, Tracer
from hush.core.tracing.flush_worker import FlushWorker, _merge_tags


# ---------------------------------------------------------------------------
# Helper — wait for and read trace files written by LocalTracer
# ---------------------------------------------------------------------------
def _wait_for_trace(trace_dir: Path, request_id: str, timeout: float = 5.0) -> dict:
    """Wait for a trace JSON file to appear and return its contents."""
    filepath = trace_dir / f"{request_id}.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if filepath.exists():
            return json.loads(filepath.read_text())
        time.sleep(0.05)
    raise TimeoutError(f"Trace file {filepath} not written within {timeout}s")


def _list_traces(trace_dir: Path, timeout: float = 2.0) -> list[dict]:
    """Wait briefly and return all trace JSON files in the directory."""
    time.sleep(timeout)
    traces = []
    for f in sorted(trace_dir.glob("*.json")):
        traces.append(json.loads(f.read_text()))
    return traces


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
# Test: LocalTracer — file writing
# ---------------------------------------------------------------------------
class TestLocalTracer:
    def test_flush_writes_json_file(self, tmp_path):
        """LocalTracer.flush() writes a {request_id}.json file."""
        tracer = LocalTracer(path=str(tmp_path))
        trace_data = {
            "request_id": "test-001",
            "workflow_name": "test-wf",
            "nodes": [],
            "tags": [],
        }
        tracer.flush(trace_data)

        filepath = tmp_path / "test-001.json"
        assert filepath.exists()
        data = json.loads(filepath.read_text())
        assert data["request_id"] == "test-001"
        assert data["workflow_name"] == "test-wf"

    def test_flush_creates_directory(self, tmp_path):
        """LocalTracer creates the trace directory if it doesn't exist."""
        nested = tmp_path / "deep" / "nested" / "traces"
        tracer = LocalTracer(path=str(nested))
        tracer.flush({"request_id": "dir-test", "nodes": []})
        assert (nested / "dir-test.json").exists()

    def test_repr(self, tmp_path):
        tracer = LocalTracer(path=str(tmp_path))
        assert "LocalTracer" in repr(tracer)


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
    async def test_flush_worker_submits_to_tracer(self, tmp_path):
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

        tracer = LocalTracer(path=str(tmp_path), tags=["static-tag"])
        worker = FlushWorker(max_workers=1)
        try:
            worker.submit([tracer], TraceCollector(graph), state)
            data = _wait_for_trace(tmp_path, "fw-001")

            assert data["request_id"] == "fw-001"
            assert data["workflow_name"] == "fw-wf"
            assert "static-tag" in data["tags"]
        finally:
            worker.shutdown()

    @pytest.mark.asyncio
    async def test_flush_worker_merges_dynamic_and_static_tags(self, tmp_path):
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

        tracer = LocalTracer(path=str(tmp_path), tags=["static-tag"])
        worker = FlushWorker(max_workers=1)
        try:
            worker.submit([tracer], TraceCollector(graph), state)
            data = _wait_for_trace(tmp_path, "fw-002")

            tags = data["tags"]
            assert "static-tag" in tags
            assert "dynamic-tag" in tags
            # Static first, then dynamic
            assert tags.index("static-tag") < tags.index("dynamic-tag")
        finally:
            worker.shutdown()

    @pytest.mark.asyncio
    async def test_flush_worker_multiple_tracers(self, tmp_path):
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

        dir1 = tmp_path / "tracer1"
        dir2 = tmp_path / "tracer2"
        t1 = LocalTracer(path=str(dir1), tags=["tracer-1"])
        t2 = LocalTracer(path=str(dir2), tags=["tracer-2"])

        worker = FlushWorker(max_workers=1)
        try:
            worker.submit([t1, t2], TraceCollector(graph), state)
            data1 = _wait_for_trace(dir1, "fw-003")
            data2 = _wait_for_trace(dir2, "fw-003")

            assert "tracer-1" in data1["tags"]
            assert "tracer-2" in data2["tags"]
        finally:
            worker.shutdown()


# ---------------------------------------------------------------------------
# Test: Engine integration with tracer= constructor parameter
# ---------------------------------------------------------------------------
class TestEngineWithTracers:
    @pytest.mark.asyncio
    async def test_engine_runs_with_tracers(self, tmp_path):
        """Engine accepts tracer= in constructor and workflow result is correct."""
        with GraphOp(name="engine-wf") as graph:
            node = FuncOp(
                name="add",
                code_fn=lambda x: {"result": x + 10},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        tracer = LocalTracer(path=str(tmp_path))
        engine = Hush(graph, tracer=tracer)

        result = await engine.run(inputs={"x": 5}, request_id="eng-001")

        # Workflow result is correct
        assert result["result"] == 15

        # Wait for background flush and read trace file
        data = _wait_for_trace(tmp_path, "eng-001")

        assert data["request_id"] == "eng-001"
        assert data["workflow_name"] == "engine-wf"
        assert len(data["nodes"]) >= 2  # graph + node

    @pytest.mark.asyncio
    async def test_engine_multiple_runs(self, tmp_path):
        """Multiple engine.run() calls each produce separate trace files."""
        with GraphOp(name="multi-run-wf") as graph:
            node = FuncOp(
                name="double",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        tracer = LocalTracer(path=str(tmp_path))
        engine = Hush(graph, tracer=tracer)

        for i in range(3):
            result = await engine.run(inputs={"x": i}, request_id=f"multi-{i}")
            assert result["result"] == i * 2

        # Wait and verify all 3 trace files
        traces = _list_traces(tmp_path)
        assert len(traces) == 3
        req_ids = {d["request_id"] for d in traces}
        assert req_ids == {"multi-0", "multi-1", "multi-2"}

    @pytest.mark.asyncio
    async def test_engine_with_user_session_ids(self, tmp_path):
        """Engine passes user_id, session_id through to trace data."""
        with GraphOp(name="ids-wf") as graph:
            node = FuncOp(
                name="echo",
                code_fn=lambda data: {"out": data},
                inputs={"data": PARENT["data"]},
                outputs={"out": PARENT},
            )
            START >> node >> END

        tracer = LocalTracer(path=str(tmp_path))
        engine = Hush(graph, tracer=tracer)

        await engine.run(
            inputs={"data": "hello"},
            request_id="ids-001",
            user_id="user-abc",
            session_id="sess-xyz",
        )

        data = _wait_for_trace(tmp_path, "ids-001")
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
    async def test_static_tags_only(self, tmp_path):
        """Static tags from tracer appear in flushed data."""
        with GraphOp(name="static-wf") as graph:
            node = FuncOp(
                name="noop",
                code_fn=lambda x: {"result": x},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        tracer = LocalTracer(path=str(tmp_path), tags=["prod", "critical"])
        engine = Hush(graph, tracer=tracer)

        await engine.run(inputs={"x": 1}, request_id="static-001")

        data = _wait_for_trace(tmp_path, "static-001")
        assert "prod" in data["tags"]
        assert "critical" in data["tags"]

    @pytest.mark.asyncio
    async def test_dynamic_tags_only(self, tmp_path):
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

        tracer = LocalTracer(path=str(tmp_path))
        engine = Hush(graph, tracer=tracer)

        await engine.run(inputs={"x": 10}, request_id="dynamic-001")

        data = _wait_for_trace(tmp_path, "dynamic-001")
        assert "computed" in data["tags"]
        assert "high-value" in data["tags"]

    @pytest.mark.asyncio
    async def test_merged_tags(self, tmp_path):
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

        tracer = LocalTracer(path=str(tmp_path), tags=["static-tag", "env:test"])
        engine = Hush(graph, tracer=tracer)

        await engine.run(inputs={"x": 1}, request_id="merged-001")

        data = _wait_for_trace(tmp_path, "merged-001")
        tags = data["tags"]
        assert "static-tag" in tags
        assert "env:test" in tags
        assert "dynamic-tag" in tags
        assert "runtime" in tags

    @pytest.mark.asyncio
    async def test_duplicate_tags_deduplicated(self, tmp_path):
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

        tracer = LocalTracer(path=str(tmp_path), tags=["shared-tag", "unique-static"])
        engine = Hush(graph, tracer=tracer)

        await engine.run(inputs={"x": 1}, request_id="dedup-001")

        data = _wait_for_trace(tmp_path, "dedup-001")
        tags = data["tags"]
        assert tags.count("shared-tag") == 1
        assert "unique-static" in tags
        assert "unique-dynamic" in tags


# ---------------------------------------------------------------------------
# Test: Non-blocking behavior
# ---------------------------------------------------------------------------
class TestTracerNonBlocking:
    @pytest.mark.asyncio
    async def test_tracers_do_not_block_workflow(self, tmp_path):
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
            r = {}
            async for _, r in e1.run(inputs={"x": i}, request_id=f"no-{i}"):
                pass
            assert r["result"] == i * 2
        time_without = time.perf_counter() - t0

        # With tracers (real LocalTracer writing to disk)
        g2 = create_graph()
        tracer = LocalTracer(path=str(tmp_path))
        e2 = Hush(g2, tracer=tracer)
        t0 = time.perf_counter()
        for i in range(NUM_REQUESTS):
            r = {}
            async for _, r in e2.run(inputs={"x": i}, request_id=f"yes-{i}"):
                pass
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
    async def test_tracer_with_generator_iteration(self, tmp_path):
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

        tracer = LocalTracer(path=str(tmp_path))
        engine = Hush(outer, tracer=tracer)
        await engine.run(inputs={"items": [1, 2, 3]}, request_id="iter-001")

        data = _wait_for_trace(tmp_path, "iter-001")
        # Should have nodes for graph and ops
        assert len(data["nodes"]) >= 2
