"""Tests for the streaming-aware TraceCollector.

Covers:
    - Batch-only workflows (no streaming, no regression)
    - Streaming workflows (generator → downstream, kind/lineage derivation)
    - TraceSummary correctness
    - Context lineage (parent_context, spawned_by, depth)
    - collect_tree(): named contexts, collapse, flatten, nested graphs, callbot
"""

import asyncio

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, graph, op
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


@op
def step_a(item: int):
    return {"result_a": item + 1}


@op
def step_b(item: int):
    return {"result_b": item + 2}


@op
def step_c(result_a: int):
    return {"result_c": result_a * 10}


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
        assert any(isinstance(s, str) and s.startswith("s") for s in ctx), (
            f"Expected stream segment in context {ctx}"
        )
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
        parent = (
            tuple(sr["parent_context"])
            if isinstance(sr["parent_context"], list)
            else sr["parent_context"]
        )
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


# =========================================================================
# Helper for collect_tree tests
# =========================================================================


def _collect_tree(graph_op, inputs):
    """Run workflow and return collect_tree result."""

    async def _run():
        engine = Hush(graph_op)
        result = await engine.run(inputs=inputs)
        collector = TraceCollector()
        return collector.collect_tree(graph_op, result["$state"])

    return asyncio.get_event_loop().run_until_complete(_run())


def _nodes_by_kind(nodes, kind):
    return [n for n in nodes if n["kind"] == kind]


def _nodes_by_name(nodes, name):
    return [n for n in nodes if n["display_name"] == name]


def _children_of(nodes, parent_key):
    return [n for n in nodes if n["parent_trace_key"] == parent_key]


# =========================================================================
# Phase 2: Basic collect_tree() tests
# =========================================================================


@pytest.mark.asyncio
async def test_tree_single_downstream_collapsed():
    """gen(yields=3) >> process >> END — single child per context → collapsed."""
    with GraphOp(name="collapse_wf") as g:
        gen = gen_items(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 3})
    collector = TraceCollector()
    trace = collector.collect_tree(g, result["$state"])
    nodes = trace["nodes"]

    # No stream_context nodes — all collapsed into child
    ctx_nodes = _nodes_by_kind(nodes, "stream_context")
    assert len(ctx_nodes) == 0, (
        f"Expected 0 stream_contexts, got {[n['display_name'] for n in ctx_nodes]}"
    )

    # process should appear as proc[0], proc[1], proc[2]
    proc_nodes = [n for n in nodes if n["display_name"].startswith("proc[")]
    assert len(proc_nodes) == 3
    expected_names = {"proc[0]", "proc[1]", "proc[2]"}
    assert {n["display_name"] for n in proc_nodes} == expected_names

    # All proc nodes should be direct children of root
    root = [n for n in nodes if n["parent_trace_key"] is None]
    assert len(root) == 1
    for pn in proc_nodes:
        assert pn["parent_trace_key"] == root[0]["trace_key"]

    # Generator node exists
    gen_nodes = _nodes_by_kind(nodes, "generator")
    assert len(gen_nodes) == 1
    assert gen_nodes[0]["metadata"]["yield_count"] == 3


@pytest.mark.asyncio
async def test_tree_multi_downstream_named():
    """gen(yields=2) >> [step_a, step_b] >> END — named contexts with multiple children."""
    with GraphOp(name="multi_wf") as g:
        gen = gen_items(count=PARENT["count"])
        sa = step_a(item=gen["item"])
        sb = step_b(item=gen["item"])
        START >> gen >> [sa, sb] >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 2})
    collector = TraceCollector()
    trace = collector.collect_tree(g, result["$state"])
    nodes = trace["nodes"]

    # Stream contexts should exist (multiple children → not collapsed)
    ctx_nodes = _nodes_by_kind(nodes, "stream_context")
    assert len(ctx_nodes) == 2
    ctx_names = {n["display_name"] for n in ctx_nodes}
    assert ctx_names == {"gen:0", "gen:1"}, f"Expected gen:0/gen:1, got {ctx_names}"

    # Each context should have 2 children (step_a, step_b)
    for ctx in ctx_nodes:
        children = _children_of(nodes, ctx["trace_key"])
        child_names = {c["display_name"] for c in children}
        assert "sa" in child_names, f"Missing sa in {child_names}"
        assert "sb" in child_names, f"Missing sb in {child_names}"


@pytest.mark.asyncio
async def test_tree_chain_downstream():
    """gen(yields=2) >> a >> b >> END — chain creates named contexts."""
    with GraphOp(name="chain_wf") as g:
        gen = gen_items(count=PARENT["count"])
        a = step_a(item=gen["item"])
        b = step_c(result_a=a["result_a"])
        START >> gen >> a >> b >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 2})
    collector = TraceCollector()
    trace = collector.collect_tree(g, result["$state"])
    nodes = trace["nodes"]

    # 2 children per context → contexts exist
    ctx_nodes = _nodes_by_kind(nodes, "stream_context")
    assert len(ctx_nodes) == 2
    ctx_names = {n["display_name"] for n in ctx_nodes}
    assert ctx_names == {"gen:0", "gen:1"}

    # Each context has 2 children (a, b)
    for ctx in ctx_nodes:
        children = _children_of(nodes, ctx["trace_key"])
        assert len(children) == 2, f"Expected 2 children, got {len(children)}"


@pytest.mark.asyncio
async def test_tree_batch_only():
    """a >> b >> END — no streaming, no synthetic nodes."""
    with GraphOp(name="batch_tree_wf") as g:
        d = double(x=PARENT["x"])
        a = add_one(value=d["result"])
        START >> d >> a >> END

    engine = Hush(g)
    result = await engine.run(inputs={"x": 5})
    collector = TraceCollector()
    trace = collector.collect_tree(g, result["$state"])
    nodes = trace["nodes"]

    # No synthetic nodes
    syn_nodes = [n for n in nodes if n["op_name"] is None]
    assert len(syn_nodes) == 0

    # All real nodes are batch
    real_nodes = [n for n in nodes if n["op_name"] is not None and n["node_type"] != "trace"]
    for n in real_nodes:
        assert n["kind"] == "batch"


@pytest.mark.asyncio
async def test_tree_no_yield_records():
    """Generator yield records (generator re-executing in stream ctx) should be dropped."""
    with GraphOp(name="yield_drop_wf") as g:
        gen = gen_items(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 3})
    collector = TraceCollector()
    trace = collector.collect_tree(g, result["$state"])
    nodes = trace["nodes"]

    # gen should appear exactly once (as generator), not 1+3 times
    gen_nodes = [n for n in nodes if n["op_name"] and n["op_name"].endswith(".gen")]
    assert len(gen_nodes) == 1, f"Expected 1 gen node, got {len(gen_nodes)}"
    assert gen_nodes[0]["kind"] == "generator"


# =========================================================================
# Phase 3: Nested graph tests
# =========================================================================


@pytest.mark.asyncio
async def test_tree_nested_graph_batch():
    """prep >> sub_graph(a >> b) >> END — nested graph appears as kind=graph."""

    @graph
    def sub(val):
        a = step_a(item=val)
        b = step_c(result_a=a["result_a"])
        START >> a >> b >> END

    with GraphOp(name="nested_batch_wf") as g:
        d = double(x=PARENT["x"])
        s = sub(val=d["result"])
        START >> d >> s >> END

    engine = Hush(g)
    result = await engine.run(inputs={"x": 3})
    collector = TraceCollector()
    trace = collector.collect_tree(g, result["$state"])
    nodes = trace["nodes"]

    # sub should be kind=graph
    graph_nodes = _nodes_by_kind(nodes, "graph")
    assert len(graph_nodes) == 1
    assert graph_nodes[0]["display_name"] == "s"

    # sub's children (a, b) should be parented to it
    children = _children_of(nodes, graph_nodes[0]["trace_key"])
    child_names = {c["display_name"] for c in children}
    assert "a" in child_names
    assert "b" in child_names


@pytest.mark.asyncio
async def test_tree_nested_graph_with_streaming():
    """gen(yields=2) >> sub_graph(proc) >> END — graph inside stream context."""

    @graph
    def sub(val):
        p = process_item(item=val)
        START >> p >> END

    with GraphOp(name="nested_stream_wf") as g:
        gen = gen_items(count=PARENT["count"])
        s = sub(val=gen["item"])
        START >> gen >> s >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 2})
    collector = TraceCollector()
    trace = collector.collect_tree(g, result["$state"])
    nodes = trace["nodes"]

    # sub should appear with kind=graph, collapsed to s[0], s[1]
    graph_nodes = _nodes_by_kind(nodes, "graph")
    assert len(graph_nodes) == 2, f"Expected 2 graph nodes, got {len(graph_nodes)}"
    graph_names = {n["display_name"] for n in graph_nodes}
    assert graph_names == {"s[0]", "s[1]"}, f"Expected s[0]/s[1], got {graph_names}"

    # No stream_context nodes (all collapsed)
    ctx_nodes = _nodes_by_kind(nodes, "stream_context")
    assert len(ctx_nodes) == 0

    for gn in graph_nodes:
        # Each graph should have process_item as child
        children = _children_of(nodes, gn["trace_key"])
        assert len(children) >= 1


@pytest.mark.asyncio
async def test_tree_streaming_inside_nested_graph():
    """outer: double >> inner_graph(gen >> process) >> END — streaming scoped to inner."""

    @graph
    def inner(val):
        gen = gen_items(count=val)
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    with GraphOp(name="outer_wf") as g:
        d = double(x=PARENT["x"])
        i = inner(val=d["result"])
        START >> d >> i >> END

    engine = Hush(g)
    result = await engine.run(inputs={"x": 2})
    collector = TraceCollector()
    trace = collector.collect_tree(g, result["$state"])
    nodes = trace["nodes"]

    # inner should be kind=graph
    graph_nodes = _nodes_by_kind(nodes, "graph")
    assert len(graph_nodes) == 1
    assert graph_nodes[0]["display_name"] == "i"

    # Generator should be inside the inner graph
    gen_nodes = _nodes_by_kind(nodes, "generator")
    assert len(gen_nodes) == 1

    # No orphaned nodes
    all_keys = {n["trace_key"] for n in nodes}
    for n in nodes:
        if n["parent_trace_key"] is not None:
            assert n["parent_trace_key"] in all_keys, (
                f"Orphan: {n['display_name']} parent={n['parent_trace_key']}"
            )


@pytest.mark.asyncio
async def test_tree_json_serializable():
    """collect_tree output should be JSON-serializable."""
    import json

    with GraphOp(name="json_tree_wf") as g:
        gen = gen_items(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 2})
    collector = TraceCollector()
    trace = collector.collect_tree(g, result["$state"])

    json_str = json.dumps(trace, default=str)
    assert len(json_str) > 0
    parsed = json.loads(json_str)
    assert parsed["workflow_name"] == "json_tree_wf"
    assert len(parsed["nodes"]) >= 3


# =========================================================================
# Phase 4: Callbot integration test
# =========================================================================


@op
async def customer_audio(sample_count: int):
    """Simulate mic input — yields fixed-size 32ms audio chunks."""
    for i in range(sample_count):
        yield {"audio": f"chunk_{i}", "timestamp_ms": i * 32}


@op
async def vad_op(audio: str, timestamp_ms: int):
    """VAD — N-to-M generator. Silence yields nothing, speech yields 1 segment."""
    speech_timestamps = {64, 128}  # chunks 2 and 4 have speech
    if timestamp_ms in speech_timestamps:
        yield {
            "segment": f"speech_{audio}",
            "start_ms": timestamp_ms,
            "end_ms": timestamp_ms + 32,
        }


@op
def stt_op(segment: str, start_ms: int, end_ms: int):
    return {"transcript": f"text:{segment}"}


@op
def classify_op(transcript: str):
    return {"intent": "greeting", "confidence": 0.9}


@op
def handle_op(intent: str, transcript: str):
    return {"response": f"Hello from {intent}!"}


@op
async def tts_op(response: str):
    """TTS — yields audio chunks."""
    for i, word in enumerate(response.split()):
        yield {"audio_out": f"tts_{i}_{word}", "index": i}


@pytest.mark.asyncio
async def test_callbot_streaming_trace():
    """Full callbot pipeline: audio → vad → stt → router → tts.

    Verifies:
    - Named contexts (audio:N)
    - Zero-yield pruning (silence chunks gone)
    - Single-yield flattening (vad:0 inner ctx removed)
    - Nested graph (router)
    - No orphaned nodes
    """

    @graph
    def router(transcript):
        c = classify_op(transcript=transcript)
        h = handle_op(intent=c["intent"], transcript=transcript)
        START >> c >> h >> END

    with GraphOp(name="callbot") as g:
        audio = customer_audio(sample_count=PARENT["samples"])
        v = vad_op(audio=audio["audio"], timestamp_ms=audio["timestamp_ms"])
        transcribe = stt_op(segment=v["segment"], start_ms=v["start_ms"], end_ms=v["end_ms"])
        r = router(transcript=transcribe["transcript"])
        speak = tts_op(response=r["response"])
        START >> audio >> v >> transcribe >> r >> speak >> END

    engine = Hush(g)
    result = await engine.run(inputs={"samples": 5})
    collector = TraceCollector()
    trace = collector.collect_tree(g, result["$state"])
    nodes = trace["nodes"]

    # 1. Root
    root = [n for n in nodes if n["node_type"] == "trace"]
    assert len(root) == 1
    assert root[0]["display_name"] == "callbot"

    # 2. audio generator at root
    gen_nodes = _nodes_by_kind(nodes, "generator")
    audio_gens = [n for n in gen_nodes if n["display_name"] == "audio"]
    assert len(audio_gens) == 1
    assert audio_gens[0]["metadata"]["yield_count"] == 5

    # 3. Only 2 outer contexts (speech at chunks 2 and 4)
    outer_ctx = [
        n for n in nodes if n["kind"] == "stream_context" and n["display_name"].startswith("audio:")
    ]
    assert len(outer_ctx) == 2
    assert {n["display_name"] for n in outer_ctx} == {"audio:2", "audio:4"}

    # 4. Zero-yield VAD executions (silence chunks) are gone
    vad_nodes = [n for n in nodes if n["op_name"] and n["op_name"].endswith(".v")]
    for vn in vad_nodes:
        if vn["kind"] == "generator":
            assert vn["metadata"].get("yield_count", 0) > 0

    # 5. Inner contexts flattened (v yields=1 → no v:0 nesting)
    inner_ctx = [
        n for n in nodes if n["kind"] == "stream_context" and n["display_name"].startswith("v:")
    ]
    assert len(inner_ctx) == 0, f"Expected 0 inner v:N contexts, got {inner_ctx}"

    # 6. Each audio:N context contains v, transcribe, router, speak
    for ctx in outer_ctx:
        children = _children_of(nodes, ctx["trace_key"])
        child_names = {c["display_name"] for c in children}
        assert "v" in child_names, f"Missing v in {child_names}"
        assert "transcribe" in child_names, f"Missing transcribe in {child_names}"
        assert "r" in child_names, f"Missing r in {child_names}"
        assert "speak" in child_names, f"Missing speak in {child_names}"

    # 7. router is kind=graph with nested children
    router_nodes = [n for n in nodes if n["kind"] == "graph" and n["display_name"] == "r"]
    assert len(router_nodes) == 2  # one per speech chunk
    for rn in router_nodes:
        router_children = _children_of(nodes, rn["trace_key"])
        assert len(router_children) >= 2  # classify + handle

    # 8. No orphaned nodes
    all_keys = {n["trace_key"] for n in nodes}
    for n in nodes:
        if n["parent_trace_key"] is not None:
            assert n["parent_trace_key"] in all_keys, (
                f"Orphan: {n['display_name']} parent={n['parent_trace_key']}"
            )

    # 9. JSON serializable
    import json

    json.dumps(trace, default=str)
