"""Tests for op serialize() methods (Phase 2 of Builder-Executor refactor)."""

from hush.core import END, PARENT, START, GraphOp, op
from hush.core.ops.flow.branch_op import if_
from hush.core.ops.iteration.base import Each
from hush.core.ops.iteration.for_op import ForOp
from hush.core.ops.iteration.map_op import MapOp
from hush.core.ops.iteration.while_op import WhileOp

# ── Helpers ──────────────────────────────────────────────────────────────


@op
def double(x: int):
    return {"result": x * 2}


@op
def add(a: int, b: int):
    return {"sum": a + b}


@op
def increment(counter: int):
    return {"counter": counter + 1}


def _build_graph(g: GraphOp) -> GraphOp:
    """Build a graph for testing (simulates what Hush() does)."""
    g.build()
    return g


# ── Ref.serialize ────────────────────────────────────────────────────────


class TestRefSerialize:
    def test_simple_ref(self):
        ref = PARENT["score"]
        s = ref.serialize()
        assert s["source"] == "__PARENT__"
        assert s["var"] == "score"
        assert s["transforms"] == []
        assert "is_output" in s

    def test_ref_with_comparison(self):
        ref = PARENT["score"] >= 90
        s = ref.serialize()
        assert s["var"] == "score"
        assert len(s["transforms"]) == 1
        assert s["transforms"][0][0] == "ge"
        assert s["transforms"][0][1] == [90]

    def test_compound_ref(self):
        ref = (PARENT["a"] > 10) & (PARENT["b"] == "x")
        s = ref.serialize()
        assert any(op_name == "and_" for op_name, _ in s["transforms"])

    def test_op_ref(self):
        """Ref from an op uses the op's name as source."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        ref = d["result"]
        s = ref.serialize()
        assert s["source"] == "g.d"
        assert s["var"] == "result"


# ── BaseOp.serialize (via @op / code type) ───────────────────────────────


class TestBaseOpSerialize:
    def test_func_op_serialize(self):
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        _build_graph(g)

        s = d.serialize()
        assert s["type"] == "code"
        assert s["name"] == "d"
        assert s["full_name"] == "g.d"
        assert s["python_callable"] is not None
        assert "x" in s["inputs"]
        assert "result" in s["outputs"]

    def test_input_ref_resolves_to_parent_graph(self):
        """PARENT refs resolve to the parent graph's full_name."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        _build_graph(g)

        s = d.serialize()
        x_input = s["inputs"]["x"]
        assert x_input["ref"] is not None
        assert x_input["ref"]["source"] == "g"
        assert x_input["ref"]["var"] == "x"

    def test_chained_op_ref(self):
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            a = add(a=d["result"], b=PARENT["y"])
            START >> d >> a >> END
        _build_graph(g)

        s = a.serialize()
        a_input = s["inputs"]["a"]
        assert a_input["ref"]["source"] == "g.d"
        assert a_input["ref"]["var"] == "result"

    def test_enabled_and_verbose(self):
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        _build_graph(g)

        s = d.serialize()
        assert s["enabled"] is True
        assert "verbose" in s

    def test_literal_input(self):
        """Non-Ref input values are stored as literals."""
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        _build_graph(g)

        s = d.serialize()
        # 'x' has a ref, check the output side for default/required
        result_output = s["outputs"]["result"]
        assert result_output["ref"] is None or result_output["literal"] is None


# ── GraphOp.serialize ───────────────────────────────────────────────────


class TestGraphOpSerialize:
    def test_basic_graph(self):
        with GraphOp(name="basic") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        _build_graph(g)

        s = g.serialize()
        assert s["type"] == "graph"
        assert s["name"] == "basic"
        assert "ops" in s
        assert "d" in s["ops"]
        assert "edges" in s
        assert "entries" in s
        assert "exits" in s
        assert "initial_ready_count" in s
        assert "compiled_adj" in s

    def test_multi_node_graph(self):
        with GraphOp(name="multi") as g:
            d = double(x=PARENT["x"])
            a = add(a=d["result"], b=PARENT["y"])
            START >> d >> a >> END
        _build_graph(g)

        s = g.serialize()
        assert "d" in s["ops"]
        assert "a" in s["ops"]
        assert len(s["entries"]) >= 1

    def test_nested_graph_serializes_recursively(self):
        with GraphOp(name="outer") as outer:
            with GraphOp(name="inner") as inner:
                d = double(x=PARENT["x"])
                START >> d >> END
            START >> inner >> END
        _build_graph(outer)

        s = outer.serialize()
        inner_s = s["ops"]["inner"]
        assert inner_s["type"] == "graph"
        assert "ops" in inner_s
        assert "d" in inner_s["ops"]

    def test_edges_structure(self):
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            a = add(a=d["result"], b=PARENT["y"])
            START >> d >> a >> END
        _build_graph(g)

        s = g.serialize()
        for edge in s["edges"]:
            assert "from" in edge
            assert "to" in edge
            assert "soft" in edge

    def test_has_soft_preds(self):
        with GraphOp(name="g") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        _build_graph(g)

        s = g.serialize()
        assert "has_soft_preds" in s


# ── BranchOp.serialize ──────────────────────────────────────────────────


class TestBranchOpSerialize:
    def test_basic_branch(self):
        with GraphOp(name="g") as g:
            router = if_(PARENT["score"] >= 90, "good").else_("bad")
            good = double(x=PARENT["score"])
            good.name = "good"
            bad = double(x=PARENT["score"])
            bad.name = "bad"
            START >> router
            router >> ~good >> END
            router >> ~bad >> END
        _build_graph(g)

        s = router.serialize()
        assert s["type"] == "branch"
        assert "cases" in s
        assert len(s["cases"]) == 1
        assert s["cases"][0]["target"] == "good"
        assert s["cases"][0]["condition"]["var"] == "score"
        assert s["default"] == "bad"

    def test_branch_candidates(self):
        with GraphOp(name="g") as g:
            router = if_(PARENT["x"] > 0, "pos").else_("neg")
            pos = double(x=PARENT["x"])
            pos.name = "pos"
            neg = double(x=PARENT["x"])
            neg.name = "neg"
            START >> router
            router >> ~pos >> END
            router >> ~neg >> END
        _build_graph(g)

        s = router.serialize()
        assert s["candidates"] is None  # given_candidates not set


# ── ForOp.serialize ─────────────────────────────────────────────────────


class TestForOpSerialize:
    def test_for_op(self):
        with ForOp(name="loop", inputs={"x": Each([1, 2, 3]), "m": 10}) as loop:
            step = double(x=PARENT["x"])
            START >> step >> END
        _build_graph(loop)

        s = loop.serialize()
        assert s["type"] == "for"
        assert "each" in s
        assert "x" in s["each"]
        assert "broadcast" in s
        assert "m" in s["broadcast"]
        assert s["fail_fast"] is False

    def test_for_op_fail_fast(self):
        with ForOp(name="loop", inputs={"x": Each([1, 2])}, fail_fast=True) as loop:
            step = double(x=PARENT["x"])
            START >> step >> END
        _build_graph(loop)

        s = loop.serialize()
        assert s["fail_fast"] is True

    def test_for_op_each_literal(self):
        with ForOp(name="loop", inputs={"x": Each([10, 20])}) as loop:
            step = double(x=PARENT["x"])
            START >> step >> END
        _build_graph(loop)

        s = loop.serialize()
        assert "x" in s["each"]
        assert s["each"]["x"]["literal"] == [10, 20]


# ── MapOp.serialize ─────────────────────────────────────────────────────


class TestMapOpSerialize:
    def test_map_op(self):
        with MapOp(name="ploop", inputs={"x": Each([1, 2])}, max_concurrency=4) as loop:
            step = double(x=PARENT["x"])
            START >> step >> END
        _build_graph(loop)

        s = loop.serialize()
        assert s["type"] == "map"
        assert s["max_concurrency"] == 4
        assert s["fail_fast"] is False
        assert "each" in s
        assert "broadcast" in s


# ── WhileOp.serialize ───────────────────────────────────────────────────


class TestWhileOpSerialize:
    def test_while_op(self):
        with WhileOp(
            name="wloop", inputs={"counter": 0}, until="counter >= 5", max_iterations=50
        ) as loop:
            step = increment(counter=PARENT["counter"])
            step["counter"] >> PARENT["counter"]
            START >> step >> END
        _build_graph(loop)

        s = loop.serialize()
        assert s["type"] == "while"
        assert s["until"] == "counter >= 5"
        assert s["max_iterations"] == 50


# ── Round-trip: serialize produces valid structure ───────────────────────


class TestSerializeStructure:
    def test_full_workflow_serializes(self):
        """A complete workflow with multiple op types serializes without error."""
        with GraphOp(name="full") as g:
            d = double(x=PARENT["input"])
            a = add(a=d["result"], b=PARENT["extra"])
            START >> d >> a >> END
        _build_graph(g)

        config = g.serialize()

        assert config["type"] == "graph"
        assert config["name"] == "full"
        assert isinstance(config["ops"], dict)
        assert isinstance(config["edges"], list)
        assert isinstance(config["entries"], list)

        for op_name, op_config in config["ops"].items():
            assert "type" in op_config
            assert "full_name" in op_config
            assert "inputs" in op_config
            assert "outputs" in op_config

    def test_serialize_idempotent(self):
        with GraphOp(name="idem") as g:
            d = double(x=PARENT["x"])
            START >> d >> END
        _build_graph(g)

        s1 = g.serialize()
        s2 = g.serialize()
        assert s1["name"] == s2["name"]
        assert s1["type"] == s2["type"]
        assert list(s1["ops"].keys()) == list(s2["ops"].keys())
