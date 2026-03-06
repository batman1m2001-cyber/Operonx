"""Tests for the streaming-aware TraceCollector.

Covers:
    - Batch-only workflows (no streaming, no regression)
    - Streaming workflows (generator → downstream, kind/lineage derivation)
    - TraceSummary correctness
    - Context lineage (parent_context, spawned_by, depth)
"""

import asyncio

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, op
from hush.core.tracing.collector import TraceCollector


# =========================================================================
# Test ops
# =========================================================================


@op
def double(x: int):
    return {"result": x * 2}


@op
def add_one(value: int):
    return {"result": value + 1}


@op
async def gen_items(count: int):
    """Generator that yields count items."""
    for i in range(count):
        yield {"item": i}


@op
def process_item(item: int):
    return {"result": item * 10}


# =========================================================================
# Test: Batch-only (no regression)
# =========================================================================


@pytest.mark.asyncio
async def test_batch_only_trace():
    """Batch workflow: all records should be kind='batch', no streaming metadata."""
    with GraphOp(name="batch_wf") as graph:
        d = double(x=PARENT["x"])
        a = add_one(value=d["result"])
        START >> d >> a >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"x": 5})
    state = result["$state"]
    assert result["result"] == 11  # 5*2 + 1

    # Collect trace
    collector = TraceCollector()
    trace = collector.collect(graph, state)

    assert trace["workflow_name"] == "batch_wf"
    assert trace["request_id"] is not None

    # Graph structure should include the root + 2 child ops
    assert len(trace["graph_structure"]) == 3  # batch_wf, d, a

    # Records: 2 ops executed (d and a), not the graph itself
    records = trace["records"]
    op_records = [r for r in records if r["kind"] == "batch"]
    assert len(op_records) >= 2

    # All should be kind="batch"
    for r in records:
        assert r["kind"] == "batch", f"Expected batch, got {r['kind']} for {r['op_name']}"
        assert r["yield_count"] is None
        assert r["spawned_by"] is None

    # Summary
    summary = trace["summary"]
    assert summary["stream_count"] == 0
    assert summary["total_yields"] == 0
    assert summary["total_records"] >= 2


# =========================================================================
# Test: Streaming workflow
# =========================================================================


@pytest.mark.asyncio
async def test_streaming_trace():
    """Streaming workflow: generator + downstream. Verify kind and lineage."""
    with GraphOp(name="stream_wf") as graph:
        gen = gen_items(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"count": 3})
    state = result["$state"]
    assert len(result["result"]) == 3
    assert sorted(result["result"]) == [0, 10, 20]

    # Collect trace
    collector = TraceCollector()
    trace = collector.collect(graph, state)

    records = trace["records"]

    # Find generator record
    gen_records = [r for r in records if r["kind"] == "generator"]
    assert len(gen_records) == 1, f"Expected 1 generator record, got {len(gen_records)}"
    gen_rec = gen_records[0]
    assert gen_rec["op_name"] == "stream_wf.gen"
    assert gen_rec["yield_count"] == 3
    assert gen_rec["depth"] == 0  # generator is at depth 0

    # Find stream_item records (downstream op at stream contexts)
    stream_records = [r for r in records if r["kind"] == "stream_item"]
    assert len(stream_records) == 3, f"Expected 3 stream_items, got {len(stream_records)}"

    for sr in stream_records:
        assert sr["op_name"] == "stream_wf.proc"
        assert sr["context"] is not None
        # Context should have a stream segment
        ctx = sr["context"]
        assert isinstance(ctx, (list, tuple))
        assert any(
            isinstance(s, str) and s.startswith("s") for s in ctx
        ), f"Expected stream segment in context {ctx}"
        # Parent context should exist
        assert sr["parent_context"] is not None
        # spawned_by should point to the generator
        assert sr["spawned_by"] == "stream_wf.gen"
        # Depth should be 1 (downstream of generator at depth 0)
        assert sr["depth"] == 1

    # Summary
    summary = trace["summary"]
    assert summary["stream_count"] == 1
    assert summary["total_yields"] == 3
    assert summary["total_records"] >= 4  # 1 generator + 3 stream_items + graph


# =========================================================================
# Test: Summary aggregation
# =========================================================================


@pytest.mark.asyncio
async def test_summary_fields():
    """Verify TraceSummary has correct aggregated values."""
    with GraphOp(name="summary_wf") as graph:
        gen = gen_items(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"count": 5})
    state = result["$state"]

    collector = TraceCollector()
    trace = collector.collect(graph, state)

    summary = trace["summary"]
    assert summary["total_ops"] == 3  # summary_wf + gen + proc
    assert summary["stream_count"] == 1
    assert summary["total_yields"] == 5
    assert summary["total_duration_ms"] > 0
    assert summary["error_count"] == 0
    assert summary["loop_iterations"] == 0


# =========================================================================
# Test: Context lineage correctness
# =========================================================================


@pytest.mark.asyncio
async def test_context_lineage():
    """Verify parent_context is correct tuple slicing."""
    with GraphOp(name="lineage_wf") as graph:
        gen = gen_items(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"count": 2})
    state = result["$state"]

    collector = TraceCollector()
    trace = collector.collect(graph, state)
    records = trace["records"]

    stream_records = [r for r in records if r["kind"] == "stream_item"]
    for sr in stream_records:
        ctx = tuple(sr["context"]) if isinstance(sr["context"], list) else sr["context"]
        parent = tuple(sr["parent_context"]) if isinstance(sr["parent_context"], list) else sr["parent_context"]
        # parent_context should be ctx[:-1]
        assert parent == ctx[:-1], f"parent_context {parent} != ctx[:-1] {ctx[:-1]}"


# =========================================================================
# Test: Trace output serializable (for LocalTracer)
# =========================================================================


@pytest.mark.asyncio
async def test_trace_json_serializable():
    """Verify trace output can be serialized to JSON (for LocalTracer)."""
    import json

    with GraphOp(name="json_wf") as graph:
        gen = gen_items(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"count": 2})
    state = result["$state"]

    collector = TraceCollector()
    trace = collector.collect(graph, state)

    # Should serialize without error (tuples become lists in JSON)
    json_str = json.dumps(trace, default=str)
    assert len(json_str) > 0

    # Round-trip
    parsed = json.loads(json_str)
    assert parsed["workflow_name"] == "json_wf"
    assert len(parsed["records"]) >= 3  # 1 gen + 2 stream_items + graph
