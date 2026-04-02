"""Tests for the TraceCollector.

Covers:
    - Batch-only workflows (no streaming, no synthetic nodes)
    - Streaming workflows (context grouping with synthetic [N] nodes)
    - Skip pending (generators with yield_count==0 removed by default)
    - TraceSummary correctness
    - Nested graphs (batch and streaming)
    - Callbot pipeline (multi-level generators, skip pending)
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
# Helpers
# =========================================================================


def _nodes_by_kind(nodes, kind):
    return [n for n in nodes if n["kind"] == kind]


def _nodes_by_name(nodes, name):
    return [n for n in nodes if n["display_name"] == name]


def _children_of(nodes, parent_key):
    return [n for n in nodes if n["parent_trace_key"] == parent_key]


def _synthetic_nodes(nodes):
    return [n for n in nodes if n["op_name"] is None]


# =========================================================================
# Test: Batch-only (no regression)
# =========================================================================


@pytest.mark.asyncio
async def test_batch_only_trace():
    """Batch workflow: all nodes should be kind='batch', no synthetic nodes."""
    with GraphOp(name="batch_wf") as g:
        d = double(x=PARENT["x"])
        a = add_one(value=d["result"])
        START >> d >> a >> END

    engine = Hush(g)
    result = await engine.run(inputs={"x": 5})
    state = result["$state"]
    assert result["result"] == 11  # 5*2 + 1

    collector = TraceCollector(g)
    trace = collector.collect(state)

    assert trace["workflow_name"] == "batch_wf"
    assert trace["request_id"] is not None

    nodes = trace["nodes"]
    real_nodes = [n for n in nodes if n["op_name"] is not None]
    assert len(real_nodes) == 3  # batch_wf, d, a

    # Non-root nodes should be kind="batch"
    child_nodes = [n for n in real_nodes if n["node_type"] != "trace"]
    for n in child_nodes:
        assert n["kind"] == "batch", f"Expected batch, got {n['kind']} for {n['op_name']}"

    # No synthetic nodes
    assert len(_synthetic_nodes(nodes)) == 0

    # Summary
    summary = trace["summary"]
    assert summary["stream_count"] == 0
    assert summary["total_yields"] == 0
    assert summary["total_records"] >= 2


# =========================================================================
# Test: Streaming workflow with context grouping
# =========================================================================


@pytest.mark.asyncio
async def test_streaming_trace():
    """Streaming workflow: generator + downstream grouped under [N] contexts."""
    with GraphOp(name="stream_wf") as g:
        gen = gen_items(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 3})
    state = result["$state"]
    # engine.run() → handle.collect() is last-value-wins for streaming;
    # use engine.start() for list accumulation.
    assert result["result"] == 20  # last value: (3-1)*10

    collector = TraceCollector(g)
    trace = collector.collect(state)
    nodes = trace["nodes"]

    # Generator node with correct yield_count
    gen_nodes = _nodes_by_kind(nodes, "generator")
    assert len(gen_nodes) == 1
    gen_node = gen_nodes[0]
    assert gen_node["op_name"] == "stream_wf.gen"
    assert gen_node["metadata"]["yield_count"] == 3

    # 3 synthetic context groups [0], [1], [2]
    ctx_nodes = _nodes_by_kind(nodes, "stream_context")
    assert len(ctx_nodes) == 3
    ctx_names = sorted(n["display_name"] for n in ctx_nodes)
    assert ctx_names == ["[0]", "[1]", "[2]"]

    # Each context group has 1 proc child
    for cn in ctx_nodes:
        children = _children_of(nodes, cn["trace_key"])
        assert len(children) == 1
        assert children[0]["display_name"] == "proc"

    # Context groups are children of the generator (not root)
    gen_children = _children_of(nodes, gen_node["trace_key"])
    gen_child_kinds = sorted(n["kind"] for n in gen_children)
    assert gen_child_kinds == ["stream_context", "stream_context", "stream_context"]

    # Summary
    summary = trace["summary"]
    assert summary["stream_count"] == 1
    assert summary["total_yields"] == 3


# =========================================================================
# Test: Summary aggregation
# =========================================================================


@pytest.mark.asyncio
async def test_summary_fields():
    """Verify TraceSummary has correct aggregated values."""
    with GraphOp(name="summary_wf") as g:
        gen = gen_items(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 5})
    state = result["$state"]

    collector = TraceCollector(g)
    trace = collector.collect(state)

    summary = trace["summary"]
    assert summary["total_ops"] == 3  # summary_wf + gen + proc
    assert summary["stream_count"] == 1
    assert summary["total_yields"] == 5
    assert summary["total_duration_ms"] > 0
    assert summary["error_count"] == 0
    assert summary["loop_iterations"] == 0


# =========================================================================
# Test: Context grouping structure
# =========================================================================


@pytest.mark.asyncio
async def test_context_group_single_downstream():
    """gen(yields=3) >> process >> END — each [N] has exactly 1 child."""
    with GraphOp(name="flat_wf") as g:
        gen = gen_items(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 3})
    collector = TraceCollector(g)
    trace = collector.collect(result["$state"])
    nodes = trace["nodes"]

    # 3 context groups, each with 1 proc
    ctx_nodes = _nodes_by_kind(nodes, "stream_context")
    assert len(ctx_nodes) == 3
    for cn in ctx_nodes:
        children = _children_of(nodes, cn["trace_key"])
        assert len(children) == 1
        assert children[0]["display_name"] == "proc"

    # Generator with yield_count=3
    gen_nodes = _nodes_by_kind(nodes, "generator")
    assert len(gen_nodes) == 1
    assert gen_nodes[0]["metadata"]["yield_count"] == 3


@pytest.mark.asyncio
async def test_context_group_multi_downstream():
    """gen(yields=2) >> [step_a, step_b] >> END — each [N] has 2 children."""
    with GraphOp(name="multi_wf") as g:
        gen = gen_items(count=PARENT["count"])
        sa = step_a(item=gen["item"])
        sb = step_b(item=gen["item"])
        START >> gen >> [sa, sb] >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 2})
    collector = TraceCollector(g)
    trace = collector.collect(result["$state"])
    nodes = trace["nodes"]

    # 2 context groups
    ctx_nodes = _nodes_by_kind(nodes, "stream_context")
    assert len(ctx_nodes) == 2

    # Each context group has step_a and step_b
    for cn in ctx_nodes:
        children = _children_of(nodes, cn["trace_key"])
        child_names = {c["display_name"] for c in children}
        assert "sa" in child_names
        assert "sb" in child_names


@pytest.mark.asyncio
async def test_context_group_chain_downstream():
    """gen(yields=2) >> a >> b >> END — each [N] has a >> b chain."""
    with GraphOp(name="chain_wf") as g:
        gen = gen_items(count=PARENT["count"])
        a = step_a(item=gen["item"])
        b = step_c(result_a=a["result_a"])
        START >> gen >> a >> b >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 2})
    collector = TraceCollector(g)
    trace = collector.collect(result["$state"])
    nodes = trace["nodes"]

    # 2 context groups
    ctx_nodes = _nodes_by_kind(nodes, "stream_context")
    assert len(ctx_nodes) == 2

    # Each context group has a and b
    for cn in ctx_nodes:
        children = _children_of(nodes, cn["trace_key"])
        child_names = {c["display_name"] for c in children}
        assert "a" in child_names
        assert "b" in child_names


@pytest.mark.asyncio
async def test_batch_only_no_synthetics():
    """a >> b >> END — no streaming, no synthetic nodes."""
    with GraphOp(name="batch_tree_wf") as g:
        d = double(x=PARENT["x"])
        a = add_one(value=d["result"])
        START >> d >> a >> END

    engine = Hush(g)
    result = await engine.run(inputs={"x": 5})
    collector = TraceCollector(g)
    trace = collector.collect(result["$state"])
    nodes = trace["nodes"]

    # No synthetic nodes
    assert len(_synthetic_nodes(nodes)) == 0

    # All real nodes are batch
    real_nodes = [n for n in nodes if n["op_name"] is not None and n["node_type"] != "trace"]
    for n in real_nodes:
        assert n["kind"] == "batch"


@pytest.mark.asyncio
async def test_generator_appears_once():
    """Generator op appears once (as kind=generator), not duplicated per yield."""
    with GraphOp(name="gen_once_wf") as g:
        gen = gen_items(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 3})
    collector = TraceCollector(g)
    trace = collector.collect(result["$state"])
    nodes = trace["nodes"]

    gen_nodes = [n for n in nodes if n["op_name"] and n["op_name"].endswith(".gen")]
    assert len(gen_nodes) == 1
    assert gen_nodes[0]["kind"] == "generator"
    assert gen_nodes[0]["metadata"]["yield_count"] == 3


# =========================================================================
# Test: Skip pending
# =========================================================================


@pytest.mark.asyncio
async def test_skip_pending_removes_zero_yield_generators():
    """Generators with yield_count==0 are removed by skip_pending=True."""

    @op
    async def sometimes_yields(x: int):
        if x > 0:
            yield {"val": x}
        # else: yields nothing

    with GraphOp(name="pending_wf") as g:
        gen = gen_items(count=PARENT["count"])
        maybe = sometimes_yields(x=gen["item"])
        proc = process_item(item=maybe["val"])
        START >> gen >> maybe >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 3})
    state = result["$state"]

    collector = TraceCollector(g)

    # With skip_pending=True (default): pending generators removed
    trace = collector.collect(state, skip_pending=True)
    nodes = trace["nodes"]
    pending = [n for n in nodes if n.get("metadata", {}).get("status") == "pending"]
    assert len(pending) == 0

    # With skip_pending=False: pending generators kept with status
    trace_all = collector.collect(state, skip_pending=False)
    nodes_all = trace_all["nodes"]
    pending_all = [n for n in nodes_all if n.get("metadata", {}).get("status") == "pending"]
    assert len(pending_all) > 0
    for p in pending_all:
        assert p["metadata"]["yield_count"] == 0


@pytest.mark.asyncio
async def test_skip_pending_removes_empty_context_groups():
    """Empty context groups (all children pending) are cascade-removed."""

    @op
    async def never_yields(x: int):
        # Generator that yields nothing
        if False:
            yield {"val": x}

    with GraphOp(name="empty_ctx_wf") as g:
        gen = gen_items(count=PARENT["count"])
        nope = never_yields(x=gen["item"])
        proc = process_item(item=nope["val"])
        START >> gen >> nope >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 3})
    state = result["$state"]

    collector = TraceCollector(g)

    # skip_pending=True: all context groups removed (all nope generators are pending)
    trace = collector.collect(state, skip_pending=True)
    nodes = trace["nodes"]
    ctx_nodes = _nodes_by_kind(nodes, "stream_context")
    assert len(ctx_nodes) == 0

    # skip_pending=False: context groups exist
    trace_all = collector.collect(state, skip_pending=False)
    nodes_all = trace_all["nodes"]
    ctx_all = _nodes_by_kind(nodes_all, "stream_context")
    assert len(ctx_all) == 3  # [0], [1], [2] from gen_items


# =========================================================================
# Nested graph tests
# =========================================================================


@pytest.mark.asyncio
async def test_nested_graph_batch():
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
    collector = TraceCollector(g)
    trace = collector.collect(result["$state"])
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
async def test_nested_graph_with_streaming():
    """gen(yields=2) >> sub_graph(proc) >> END — graph inside context group."""

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
    collector = TraceCollector(g)
    trace = collector.collect(result["$state"])
    nodes = trace["nodes"]

    # 2 context groups [0], [1]
    ctx_nodes = _nodes_by_kind(nodes, "stream_context")
    assert len(ctx_nodes) == 2

    # Each context group has sub (kind=graph) as child
    for cn in ctx_nodes:
        children = _children_of(nodes, cn["trace_key"])
        graph_children = [c for c in children if c["kind"] == "graph"]
        assert len(graph_children) == 1
        assert graph_children[0]["display_name"] == "s"

        # Each graph should have process_item as child
        for gc in graph_children:
            grandchildren = _children_of(nodes, gc["trace_key"])
            assert len(grandchildren) >= 1

    # No orphaned nodes
    all_keys = {n["trace_key"] for n in nodes}
    for n in nodes:
        if n["parent_trace_key"] is not None:
            assert n["parent_trace_key"] in all_keys, (
                f"Orphan: {n['display_name']} parent={n['parent_trace_key']}"
            )


@pytest.mark.asyncio
async def test_streaming_inside_nested_graph():
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
    collector = TraceCollector(g)
    trace = collector.collect(result["$state"])
    nodes = trace["nodes"]

    # inner should be kind=graph
    graph_nodes = _nodes_by_kind(nodes, "graph")
    assert len(graph_nodes) == 1
    assert graph_nodes[0]["display_name"] == "i"

    # Generator should be inside the inner graph
    gen_nodes = _nodes_by_kind(nodes, "generator")
    assert len(gen_nodes) == 1

    # Context groups should be children of the generator (inside inner graph)
    ctx_nodes = _nodes_by_kind(nodes, "stream_context")
    assert len(ctx_nodes) > 0
    for cn in ctx_nodes:
        # Context groups should be under the generator, not the graph directly
        assert cn["parent_trace_key"] == gen_nodes[0]["trace_key"]

    # No orphaned nodes
    all_keys = {n["trace_key"] for n in nodes}
    for n in nodes:
        if n["parent_trace_key"] is not None:
            assert n["parent_trace_key"] in all_keys, (
                f"Orphan: {n['display_name']} parent={n['parent_trace_key']}"
            )


# =========================================================================
# Test: JSON serializable
# =========================================================================


@pytest.mark.asyncio
async def test_trace_json_serializable():
    """Verify trace output can be serialized to JSON."""
    import json

    with GraphOp(name="json_wf") as g:
        gen = gen_items(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 2})
    state = result["$state"]

    collector = TraceCollector(g)
    trace = collector.collect(state)

    json_str = json.dumps(trace, default=str)
    assert len(json_str) > 0
    parsed = json.loads(json_str)
    assert parsed["workflow_name"] == "json_wf"
    assert len(parsed["nodes"]) >= 3


# =========================================================================
# Callbot integration test
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
    - Context grouping with synthetic [N] nodes
    - Skip pending removes silence VAD generators and their empty contexts
    - Generator nodes with correct yield counts
    - Nested graph (router) inside context groups
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
    collector = TraceCollector(g)
    trace = collector.collect(result["$state"])
    nodes = trace["nodes"]

    # 1. Root
    root = [n for n in nodes if n["node_type"] == "trace"]
    assert len(root) == 1
    assert root[0]["display_name"] == "callbot"

    # 2. audio generator with yield_count=5
    audio_nodes = [n for n in nodes if n["kind"] == "generator" and n["display_name"] == "audio"]
    assert len(audio_nodes) == 1
    assert audio_nodes[0]["metadata"]["yield_count"] == 5

    # 3. With skip_pending=True (default), silence VADs are removed
    # Only 2 speech VADs remain (chunks 2 and 4)
    vad_nodes = [n for n in nodes if n["op_name"] and n["op_name"].endswith(".v")]
    assert len(vad_nodes) == 2
    for vn in vad_nodes:
        assert vn["kind"] == "generator"
        assert vn["metadata"]["yield_count"] > 0

    # 4. Only 2 context groups remain ([2] and [4]), as children of audio
    audio_ctx = [
        n
        for n in nodes
        if n["kind"] == "stream_context" and n["parent_trace_key"] == audio_nodes[0]["trace_key"]
    ]
    assert len(audio_ctx) == 2
    ctx_names = sorted(n["display_name"] for n in audio_ctx)
    assert ctx_names == ["[2]", "[4]"]

    # 5. 2 stt invocations (one per speech segment)
    stt_nodes = [n for n in nodes if n["op_name"] and n["op_name"].endswith(".transcribe")]
    assert len(stt_nodes) == 2

    # 6. router is kind=graph, inside nested context groups
    router_nodes = [n for n in nodes if n["kind"] == "graph" and n["display_name"] == "r"]
    assert len(router_nodes) == 2
    for rn in router_nodes:
        router_children = _children_of(nodes, rn["trace_key"])
        assert len(router_children) >= 2  # classify + handle

    # 7. No orphaned nodes
    all_keys = {n["trace_key"] for n in nodes}
    for n in nodes:
        if n["parent_trace_key"] is not None:
            assert n["parent_trace_key"] in all_keys, (
                f"Orphan: {n['display_name']} parent={n['parent_trace_key']}"
            )

    # 8. No pending nodes (they were removed by skip_pending)
    pending = [n for n in nodes if n.get("metadata", {}).get("status") == "pending"]
    assert len(pending) == 0

    # 9. JSON serializable
    import json

    json.dumps(trace, default=str)


@pytest.mark.asyncio
async def test_callbot_skip_pending_false():
    """Callbot with skip_pending=False: all VADs and context groups kept."""

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
    collector = TraceCollector(g)
    trace = collector.collect(result["$state"], skip_pending=False)
    nodes = trace["nodes"]

    # All 5 VAD generators present
    vad_nodes = [n for n in nodes if n["op_name"] and n["op_name"].endswith(".v")]
    assert len(vad_nodes) == 5

    # 3 silence VADs have status=pending
    pending_vads = [vn for vn in vad_nodes if vn["metadata"].get("status") == "pending"]
    assert len(pending_vads) == 3

    # All 5 context groups present, as children of audio
    audio_nodes = [n for n in nodes if n["kind"] == "generator" and n["display_name"] == "audio"]
    assert len(audio_nodes) == 1
    audio_ctx = [
        n
        for n in nodes
        if n["kind"] == "stream_context" and n["parent_trace_key"] == audio_nodes[0]["trace_key"]
    ]
    assert len(audio_ctx) == 5
