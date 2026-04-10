"""Tests for TraceFilter — configurable trace node filtering."""

import pytest

from hush.core.tracing.trace_filter import TraceFilter

# ---------------------------------------------------------------------------
# Helpers: build fake node dicts matching _safe_asdict(TraceNode) structure
# ---------------------------------------------------------------------------


def _node(
    trace_key: str,
    *,
    parent_trace_key: str | None = None,
    op_name: str | None = None,
    display_name: str = "",
    node_type: str = "span",
    kind: str = "batch",
    outputs: dict | None = None,
) -> dict:
    return {
        "trace_key": trace_key,
        "parent_trace_key": parent_trace_key,
        "op_name": op_name,
        "display_name": display_name or (op_name.rsplit(".", 1)[-1] if op_name else trace_key),
        "node_type": node_type,
        "kind": kind,
        "inputs": {},
        "outputs": outputs if outputs is not None else {},
        "start_time": None,
        "end_time": None,
        "duration_ms": None,
        "metadata": {},
        "model": None,
        "usage": None,
        "cost": None,
    }


# ---------------------------------------------------------------------------
# Test: no filter (passthrough)
# ---------------------------------------------------------------------------


class TestNoFilter:
    def test_empty_filter_passes_all(self):
        nodes = [
            _node("root", node_type="trace", kind="batch"),
            _node("a", parent_trace_key="root", op_name="g.a"),
            _node("b", parent_trace_key="root", op_name="g.b"),
        ]
        tf = TraceFilter()
        result = tf.apply(nodes)
        assert len(result) == 3

    def test_empty_list(self):
        tf = TraceFilter()
        assert tf.apply([]) == []


# ---------------------------------------------------------------------------
# Test: skip_empty
# ---------------------------------------------------------------------------


class TestSkipEmpty:
    def test_removes_all_none_outputs(self):
        nodes = [
            _node("root", node_type="trace"),
            _node("a", parent_trace_key="root", op_name="g.a", outputs={"x": None}),
            _node("b", parent_trace_key="root", op_name="g.b", outputs={"x": 42}),
        ]
        tf = TraceFilter(skip_empty=True)
        result = tf.apply(nodes)
        keys = [n["trace_key"] for n in result]
        assert "a" not in keys
        assert "b" in keys

    def test_removes_empty_dict_outputs(self):
        nodes = [
            _node("root", node_type="trace"),
            _node("a", parent_trace_key="root", op_name="g.a", outputs={}),
        ]
        tf = TraceFilter(skip_empty=True)
        result = tf.apply(nodes)
        assert len(result) == 1  # only root

    def test_preserves_generation_with_none_outputs(self):
        """generation node_type is protected — never filtered even if empty."""
        nodes = [
            _node("root", node_type="trace"),
            _node(
                "llm",
                parent_trace_key="root",
                op_name="g.llm",
                node_type="generation",
                outputs={"text": None},
            ),
        ]
        tf = TraceFilter(skip_empty=True)
        result = tf.apply(nodes)
        keys = [n["trace_key"] for n in result]
        assert "llm" in keys

    def test_does_not_filter_synthetic_nodes(self):
        """Synthetic nodes (op_name=None) should not be filtered by skip_empty."""
        nodes = [
            _node("root", node_type="trace"),
            _node(
                "$ctx:g:[0]",
                parent_trace_key="root",
                op_name=None,
                display_name="[0]",
                kind="stream_context",
                outputs={},
            ),
            _node("child", parent_trace_key="$ctx:g:[0]", op_name="g.child", outputs={"x": 1}),
        ]
        tf = TraceFilter(skip_empty=True)
        result = tf.apply(nodes)
        keys = [n["trace_key"] for n in result]
        assert "$ctx:g:[0]" in keys


# ---------------------------------------------------------------------------
# Test: exclude_ops
# ---------------------------------------------------------------------------


class TestExcludeOps:
    def test_exclude_by_short_name(self):
        nodes = [
            _node("root", node_type="trace"),
            _node(
                "a",
                parent_trace_key="root",
                op_name="callbot.recv_audio",
                display_name="recv_audio",
            ),
            _node(
                "b", parent_trace_key="root", op_name="callbot.classify", display_name="classify"
            ),
        ]
        tf = TraceFilter(exclude_ops=["recv_audio"])
        result = tf.apply(nodes)
        keys = [n["trace_key"] for n in result]
        assert "a" not in keys
        assert "b" in keys

    def test_exclude_by_full_name(self):
        nodes = [
            _node("root", node_type="trace"),
            _node(
                "a",
                parent_trace_key="root",
                op_name="callbot.recv_audio",
                display_name="recv_audio",
            ),
        ]
        tf = TraceFilter(exclude_ops=["callbot.recv_audio"])
        result = tf.apply(nodes)
        keys = [n["trace_key"] for n in result]
        assert "a" not in keys

    def test_exclude_does_not_affect_protected(self):
        """Even if root trace op_name matches, it should be kept."""
        nodes = [
            _node("root", node_type="trace", op_name="callbot", display_name="callbot"),
        ]
        tf = TraceFilter(exclude_ops=["callbot"])
        result = tf.apply(nodes)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Test: include_ops (whitelist)
# ---------------------------------------------------------------------------


class TestIncludeOps:
    def test_whitelist_keeps_only_listed(self):
        nodes = [
            _node("root", node_type="trace"),
            _node("a", parent_trace_key="root", op_name="g.classify", display_name="classify"),
            _node("b", parent_trace_key="root", op_name="g.recv_audio", display_name="recv_audio"),
            _node("c", parent_trace_key="root", op_name="g.denoise", display_name="denoise"),
        ]
        tf = TraceFilter(include_ops=["classify"])
        result = tf.apply(nodes)
        keys = [n["trace_key"] for n in result]
        assert "a" in keys
        assert "b" not in keys
        assert "c" not in keys

    def test_whitelist_preserves_protected(self):
        nodes = [
            _node("root", node_type="trace"),
            _node(
                "llm",
                parent_trace_key="root",
                op_name="g.llm",
                display_name="llm",
                node_type="generation",
            ),
            _node("b", parent_trace_key="root", op_name="g.other", display_name="other"),
        ]
        tf = TraceFilter(include_ops=["other"])
        result = tf.apply(nodes)
        keys = [n["trace_key"] for n in result]
        # generation is protected even though not in include_ops
        assert "llm" in keys
        assert "b" in keys


# ---------------------------------------------------------------------------
# Test: exclude_kinds
# ---------------------------------------------------------------------------


class TestExcludeKinds:
    def test_exclude_stream_context(self):
        nodes = [
            _node("root", node_type="trace"),
            _node("gen", parent_trace_key="root", op_name="g.audio", kind="generator"),
            _node(
                "$ctx:[0]",
                parent_trace_key="gen",
                op_name=None,
                display_name="[0]",
                kind="stream_context",
            ),
            _node("child", parent_trace_key="$ctx:[0]", op_name="g.child"),
        ]
        tf = TraceFilter(exclude_kinds=["stream_context"])
        result = tf.apply(nodes)
        keys = [n["trace_key"] for n in result]
        assert "$ctx:[0]" not in keys
        # child re-parented to generator
        child = next(n for n in result if n["trace_key"] == "child")
        assert child["parent_trace_key"] == "gen"


# ---------------------------------------------------------------------------
# Test: re-parenting
# ---------------------------------------------------------------------------


class TestReparenting:
    def test_children_reparented_to_grandparent(self):
        nodes = [
            _node("root", node_type="trace"),
            _node("mid", parent_trace_key="root", op_name="g.mid", display_name="mid"),
            _node("child", parent_trace_key="mid", op_name="g.child", display_name="child"),
        ]
        tf = TraceFilter(exclude_ops=["mid"])
        result = tf.apply(nodes)
        keys = [n["trace_key"] for n in result]
        assert "mid" not in keys
        child = next(n for n in result if n["trace_key"] == "child")
        assert child["parent_trace_key"] == "root"

    def test_reparent_chain(self):
        """If parent AND grandparent are both removed, child goes to great-grandparent."""
        nodes = [
            _node("root", node_type="trace"),
            _node("a", parent_trace_key="root", op_name="g.a", display_name="a"),
            _node("b", parent_trace_key="a", op_name="g.b", display_name="b"),
            _node("c", parent_trace_key="b", op_name="g.c", display_name="c"),
        ]
        tf = TraceFilter(exclude_ops=["a", "b"])
        result = tf.apply(nodes)
        keys = [n["trace_key"] for n in result]
        assert "a" not in keys
        assert "b" not in keys
        c = next(n for n in result if n["trace_key"] == "c")
        assert c["parent_trace_key"] == "root"

    def test_reparent_to_none_if_all_ancestors_removed(self):
        """If all non-root ancestors are removed, child becomes direct trace child."""
        nodes = [
            _node("root", node_type="trace"),
            _node("a", parent_trace_key="root", op_name="g.a", display_name="a", outputs={}),
            _node("b", parent_trace_key="a", op_name="g.b", display_name="b", outputs={}),
            _node(
                "llm",
                parent_trace_key="b",
                op_name="g.llm",
                display_name="llm",
                node_type="generation",
            ),
        ]
        tf = TraceFilter(skip_empty=True)
        result = tf.apply(nodes)
        llm = next(n for n in result if n["trace_key"] == "llm")
        # root is the only surviving ancestor
        assert llm["parent_trace_key"] == "root"


# ---------------------------------------------------------------------------
# Test: empty synthetic cleanup
# ---------------------------------------------------------------------------


class TestSyntheticCleanup:
    def test_removes_childless_stream_context(self):
        nodes = [
            _node("root", node_type="trace"),
            _node(
                "$ctx:[0]",
                parent_trace_key="root",
                op_name=None,
                display_name="[0]",
                kind="stream_context",
            ),
            _node(
                "a",
                parent_trace_key="$ctx:[0]",
                op_name="g.a",
                display_name="a",
                outputs={},
            ),
        ]
        tf = TraceFilter(skip_empty=True)
        result = tf.apply(nodes)
        keys = [n["trace_key"] for n in result]
        # a removed (empty), then $ctx:[0] removed (childless)
        assert "a" not in keys
        assert "$ctx:[0]" not in keys

    def test_cascading_synthetic_cleanup(self):
        """Nested synthetic nodes: if inner becomes empty, outer should also be removed."""
        nodes = [
            _node("root", node_type="trace"),
            _node(
                "$ctx:outer",
                parent_trace_key="root",
                op_name=None,
                display_name="[0]",
                kind="stream_context",
            ),
            _node(
                "$ctx:inner",
                parent_trace_key="$ctx:outer",
                op_name=None,
                display_name="[0]",
                kind="stream_context",
            ),
            _node("a", parent_trace_key="$ctx:inner", op_name="g.a", display_name="a", outputs={}),
        ]
        tf = TraceFilter(skip_empty=True)
        result = tf.apply(nodes)
        keys = [n["trace_key"] for n in result]
        assert keys == ["root"]


# ---------------------------------------------------------------------------
# Test: mutual exclusion
# ---------------------------------------------------------------------------


class TestValidation:
    def test_exclude_and_include_raises(self):
        with pytest.raises(ValueError, match="Cannot set both"):
            TraceFilter(exclude_ops=["a"], include_ops=["b"])


# ---------------------------------------------------------------------------
# Test: from_dict
# ---------------------------------------------------------------------------


class TestFromDict:
    def test_basic_parse(self):
        tf = TraceFilter.from_dict(
            {
                "skip_empty": True,
                "exclude_ops": ["recv_audio", "denoise"],
            }
        )
        assert tf.skip_empty is True
        assert tf.exclude_ops == ["recv_audio", "denoise"]
        assert tf.include_ops == []
        assert tf.protected_types == ["trace", "generation"]

    def test_unknown_keys_ignored(self):
        tf = TraceFilter.from_dict(
            {
                "skip_empty": True,
                "future_field": "whatever",
            }
        )
        assert tf.skip_empty is True

    def test_empty_dict(self):
        tf = TraceFilter.from_dict({})
        assert tf.skip_empty is False
        assert tf.exclude_ops == []


# ---------------------------------------------------------------------------
# Test: combined filters
# ---------------------------------------------------------------------------


class TestCombined:
    def test_exclude_ops_plus_skip_empty(self):
        nodes = [
            _node("root", node_type="trace"),
            _node(
                "recv",
                parent_trace_key="root",
                op_name="g.recv_audio",
                display_name="recv_audio",
                outputs={"audio": b"data"},
            ),
            _node(
                "denoise",
                parent_trace_key="root",
                op_name="g.denoise",
                display_name="denoise",
                outputs={"x": None},
            ),
            _node(
                "classify",
                parent_trace_key="root",
                op_name="g.classify",
                display_name="classify",
                node_type="generation",
                outputs={"intent": "greeting"},
            ),
        ]
        tf = TraceFilter(skip_empty=True, exclude_ops=["recv_audio"])
        result = tf.apply(nodes)
        keys = [n["trace_key"] for n in result]
        # recv_audio excluded by name, denoise excluded by skip_empty
        assert "recv" not in keys
        assert "denoise" not in keys
        # classify (generation) always kept
        assert "classify" in keys


# ---------------------------------------------------------------------------
# Test: max_io_size truncation
# ---------------------------------------------------------------------------


class TestMaxIoSize:
    def test_truncates_large_string(self):
        nodes = [
            _node("root", node_type="trace"),
            _node(
                "a",
                parent_trace_key="root",
                op_name="g.a",
                outputs={"text": "x" * 5000},
            ),
        ]
        tf = TraceFilter(max_io_size=100)
        result = tf.apply(nodes)
        a = next(n for n in result if n["trace_key"] == "a")
        assert len(a["outputs"]["text"]) < 200
        assert "truncated" in a["outputs"]["text"]

    def test_truncates_bytes(self):
        nodes = [
            _node("root", node_type="trace"),
            _node(
                "a",
                parent_trace_key="root",
                op_name="g.a",
                outputs={"raw_wav": b"\x00" * 50000},
            ),
        ]
        tf = TraceFilter(max_io_size=100)
        result = tf.apply(nodes)
        a = next(n for n in result if n["trace_key"] == "a")
        assert isinstance(a["outputs"]["raw_wav"], str)
        assert "50000 bytes" in a["outputs"]["raw_wav"]

    def test_truncates_large_list(self):
        nodes = [
            _node("root", node_type="trace"),
            _node(
                "a",
                parent_trace_key="root",
                op_name="g.a",
                outputs={"audio": list(range(10000))},
            ),
        ]
        tf = TraceFilter(max_io_size=100)
        result = tf.apply(nodes)
        a = next(n for n in result if n["trace_key"] == "a")
        assert isinstance(a["outputs"]["audio"], str)
        assert "len=10000" in a["outputs"]["audio"]

    def test_no_truncation_when_zero(self):
        nodes = [
            _node("root", node_type="trace"),
            _node(
                "a",
                parent_trace_key="root",
                op_name="g.a",
                outputs={"text": "x" * 5000},
            ),
        ]
        tf = TraceFilter(max_io_size=0)
        result = tf.apply(nodes)
        a = next(n for n in result if n["trace_key"] == "a")
        assert len(a["outputs"]["text"]) == 5000

    def test_small_values_untouched(self):
        nodes = [
            _node("root", node_type="trace"),
            _node(
                "a",
                parent_trace_key="root",
                op_name="g.a",
                outputs={"text": "hello", "num": 42},
            ),
        ]
        tf = TraceFilter(max_io_size=100)
        result = tf.apply(nodes)
        a = next(n for n in result if n["trace_key"] == "a")
        assert a["outputs"]["text"] == "hello"
        assert a["outputs"]["num"] == 42
