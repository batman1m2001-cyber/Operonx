"""Tests for labeled iteration wrappers.

Covers:
    - label() outside an op is a no-op (doesn't crash)
    - label() inside a plain @op sets no label (only async-gen ops matter)
    - label() inside an async-gen op renames stream_context wrappers to
      labeled_iter with the user-supplied display_name
    - Unlabeled yields fall back to default "[N]" stream_context
    - TraceFilter.exclude_kinds=["stream_context"] preserves labeled_iter
    - Labels survive concurrent _pump tasks (CCU safety)
"""

import asyncio

import pytest

from hush.core import END, PARENT, START, GraphOp, Hush, op
from hush.core.tracing import TraceFilter, label
from hush.core.tracing.collector import TraceCollector
from hush.core.tracing.labels import _current_gen_key

# =========================================================================
# Helpers
# =========================================================================


def _nodes_by_kind(nodes, kind):
    return [n for n in nodes if n["kind"] == kind]


# =========================================================================
# Ops
# =========================================================================


@op
async def labeled_gen(count: int):
    """Generator that labels each yield with a descriptive string."""
    for i in range(count):
        label(f"turn-{i} (kind={'audio' if i % 2 == 0 else 'silence'})")
        yield {"item": i}


@op
async def partially_labeled_gen(count: int):
    """Only label some yields — others fall back to default."""
    for i in range(count):
        if i == 1:
            label("THE SECOND ONE")
        yield {"item": i}


@op
async def unlabeled_gen(count: int):
    """No label calls — behavior should match pre-feature collector."""
    for i in range(count):
        yield {"item": i}


@op
def process_item(item: int):
    return {"result": item * 10}


# =========================================================================
# Unit: label() public API edge cases
# =========================================================================


def test_label_outside_op_is_noop():
    """Calling label() with no binding should not crash or leak."""
    label("should-not-appear")  # no store bound → silent no-op


def test_label_not_bound_still_noop():
    """With _current_gen_key unset, label() is a no-op."""
    assert _current_gen_key.get() is None
    label("ignored")


# =========================================================================
# Integration: labeled async-gen op → trace display_name
# =========================================================================


@pytest.mark.asyncio
async def test_labeled_gen_renames_stream_contexts():
    """Each stream_context wrapper gets the user's label as display_name."""
    with GraphOp(name="label_wf") as g:
        gen = labeled_gen(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={"count": 3})
    state = result["$state"]

    collector = TraceCollector(g)
    trace = collector.collect(state)
    nodes = trace["nodes"]

    # No default stream_context nodes — all have been relabeled
    ctx_nodes = _nodes_by_kind(nodes, "stream_context")
    assert ctx_nodes == [], (
        f"unexpected stream_context nodes: {[n['display_name'] for n in ctx_nodes]}"
    )

    # 3 labeled_iter wrappers with the strings from label()
    labeled_nodes = _nodes_by_kind(nodes, "labeled_iter")
    assert len(labeled_nodes) == 3
    names = sorted(n["display_name"] for n in labeled_nodes)
    assert names == [
        "turn-0 (kind=audio)",
        "turn-1 (kind=silence)",
        "turn-2 (kind=audio)",
    ]


@pytest.mark.asyncio
async def test_partially_labeled_mixes_kinds():
    """Only labeled yields become labeled_iter — unlabeled ones stay as [N]."""
    with GraphOp(name="partial_wf") as g:
        gen = partially_labeled_gen(count=3)
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={})
    state = result["$state"]

    collector = TraceCollector(g)
    trace = collector.collect(state)
    nodes = trace["nodes"]

    labeled_nodes = _nodes_by_kind(nodes, "labeled_iter")
    stream_nodes = _nodes_by_kind(nodes, "stream_context")

    assert len(labeled_nodes) == 1
    assert labeled_nodes[0]["display_name"] == "THE SECOND ONE"

    assert len(stream_nodes) == 2
    assert sorted(n["display_name"] for n in stream_nodes) == ["[0]", "[2]"]


@pytest.mark.asyncio
async def test_unlabeled_gen_unchanged_from_baseline():
    """Generators without label() calls behave exactly as before the feature."""
    with GraphOp(name="baseline_wf") as g:
        gen = unlabeled_gen(count=2)
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={})
    state = result["$state"]

    collector = TraceCollector(g)
    trace = collector.collect(state)
    nodes = trace["nodes"]

    labeled_nodes = _nodes_by_kind(nodes, "labeled_iter")
    stream_nodes = _nodes_by_kind(nodes, "stream_context")

    assert labeled_nodes == []
    assert len(stream_nodes) == 2
    assert sorted(n["display_name"] for n in stream_nodes) == ["[0]", "[1]"]


# =========================================================================
# Integration: TraceFilter.exclude_kinds preserves labeled wrappers
# =========================================================================


@pytest.mark.asyncio
async def test_exclude_stream_context_preserves_labeled():
    """exclude_kinds=[stream_context] drops only unlabeled wrappers."""
    with GraphOp(name="filter_wf") as g:
        gen = partially_labeled_gen(count=3)
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(g)
    result = await engine.run(inputs={})
    state = result["$state"]

    collector = TraceCollector(g)
    trace = collector.collect(state)

    tf = TraceFilter(exclude_kinds=["stream_context"])
    filtered = tf.apply(trace["nodes"])

    labeled_nodes = _nodes_by_kind(filtered, "labeled_iter")
    stream_nodes = _nodes_by_kind(filtered, "stream_context")

    assert len(labeled_nodes) == 1, "labeled_iter must survive the filter"
    assert stream_nodes == [], "stream_context must be dropped"


# =========================================================================
# Concurrency: multiple graph runs in parallel see independent labels
# =========================================================================


@pytest.mark.asyncio
async def test_concurrent_runs_dont_leak_labels():
    """Two concurrent engine.run() calls must not cross-contaminate labels."""
    with GraphOp(name="ccu_wf") as g:
        gen = labeled_gen(count=PARENT["count"])
        proc = process_item(item=gen["item"])
        START >> gen >> proc >> END

    engine = Hush(g)

    async def _run_and_collect(count: int):
        result = await engine.run(inputs={"count": count})
        state = result["$state"]
        trace = TraceCollector(g).collect(state)
        return [n["display_name"] for n in trace["nodes"] if n["kind"] == "labeled_iter"]

    results = await asyncio.gather(
        _run_and_collect(2),
        _run_and_collect(3),
        _run_and_collect(1),
    )

    # Each run should have exactly `count` labels, all of form "turn-{i} ..."
    assert len(results[0]) == 2
    assert len(results[1]) == 3
    assert len(results[2]) == 1
