"""A Ref buried in a container must fail loudly at construction.

Operonx wires **one param → one state cell → one pull-ref**. A `Ref`
nested inside a dict or list has no cell to live in, so before this check
it fell through to the literal branch and three things broke at once,
none of them noisily:

1. the op received the `Ref` object instead of the value,
2. no dependency edge was created, so ordering was unguaranteed,
3. `GraphOp._validate`'s cross-graph scope check never saw it — a Ref to
   an op in another graph passed hermeticity validation.

Found while probing `InterruptOp(payload={"tool": p["name"], ...})` for
the agent work: a human approving a destructive tool call was shown
`Ref` objects instead of the tool name and arguments. It is not an
InterruptOp bug — every op behaved this way.
"""

from __future__ import annotations

import pytest

from operonx.core import PARENT, op
from operonx.core.ops._params import _find_nested_ref, normalize_params
from operonx.core.states.ref import Ref

pytestmark = pytest.mark.unit


@op
def src(x: int) -> dict:
    return {"a": x, "b": x * 2}


@op
def sink(v=None, w=None) -> dict:
    return {"seen": v}


class TestRejected:
    """Every container shape that used to swallow a Ref."""

    def test_dict_value(self):
        s = src(x=1)
        with pytest.raises(TypeError, match=r"nested inside a dict"):
            sink(v={"one": s["a"]})

    def test_ref_as_dict_key_is_impossible_by_construction(self):
        """No scanner branch needed for keys: Ref overloads __eq__ to
        build branch conditions, so it is unhashable and Python rejects
        the literal before op construction runs."""
        s = src(x=1)
        with pytest.raises(TypeError, match=r"unhashable"):
            {s["a"]: "one"}  # noqa: B018

    def test_list(self):
        s = src(x=1)
        with pytest.raises(TypeError, match=r"nested inside a list"):
            sink(v=[s["a"], 2])

    def test_tuple(self):
        s = src(x=1)
        with pytest.raises(TypeError, match=r"nested inside a tuple"):
            sink(v=(s["a"],))

    def test_deeply_nested(self):
        s = src(x=1)
        with pytest.raises(TypeError, match=r"v\['outer'\]\['inner'\]"):
            sink(v={"outer": {"inner": s["a"]}})

    def test_parent_ref_too(self):
        """PARENT['x'] is a Ref like any other."""
        with pytest.raises(TypeError, match=r"nested inside a dict"):
            sink(v={"from_parent": PARENT["x"]})


class TestMessageIsActionable:
    def test_names_param_path_and_source(self):
        s = src(x=1)
        with pytest.raises(TypeError) as exc:
            sink(v={"one": s["a"]})
        msg = str(exc.value)

        assert "v['one']" in msg, "must name the exact path"
        assert ".a" in msg, "must name the source var"
        # Both escape hatches, so the reader does not have to guess.
        assert "its own param" in msg
        assert "upstream @op" in msg


class TestStillAccepted:
    """The forms that already worked must keep working."""

    def test_bare_ref(self):
        s = src(x=1)
        node = sink(v=s["a"])
        assert isinstance(node.inputs["v"].value, Ref)

    def test_params_mapping_is_not_a_container(self):
        """`inputs={"v": ref}` is the params mapping, not a value holding
        a Ref. callbot writes this at `src/callbot/graph.py:118` and it
        must not trip the check."""
        s = src(x=1)
        node = sink(inputs={"v": s["a"]})
        assert isinstance(node.inputs["v"].value, Ref)

    def test_plain_containers(self):
        node = sink(v={"a": 1, "b": [2, 3]}, w=[1, 2, 3])
        assert node.inputs["v"].value == {"a": 1, "b": [2, 3]}

    def test_empty_containers(self):
        node = sink(v={}, w=[])
        assert node.inputs["v"].value == {}

    def test_op_passed_directly(self):
        """`sink(v=some_op)` → Ref(some_op, "v"); not a container."""
        s = src(x=1)
        node = sink(v=s)
        assert isinstance(node.inputs["v"].value, Ref)


class TestScanner:
    """`_find_nested_ref` in isolation — cheap, total, no false positives."""

    def test_returns_none_for_ref_free_structures(self):
        assert _find_nested_ref({"a": [1, {"b": (2, 3)}]}) is None
        assert _find_nested_ref(None) is None
        assert _find_nested_ref("a string") is None

    def test_reports_first_hit_with_path(self):
        s = src(x=1)
        path, ref = _find_nested_ref({"a": {"b": [s["a"]]}})
        assert path == "['a']['b'][0]"
        assert ref.var == "a"

    def test_walk_is_not_depth_capped(self):
        """A depth cap would make the original bug reappear one level
        past it — silently, which is the exact failure mode being fixed.
        The walk is exhaustive instead."""
        s = src(x=1)
        deep = s["a"]
        for _ in range(50):
            deep = {"n": deep}
        found = _find_nested_ref(deep)
        assert found is not None, "a deeply buried Ref must still be found"
        assert found[1].var == "a"

    def test_self_referential_structure_terminates(self):
        loop: dict = {}
        loop["self"] = loop
        assert _find_nested_ref(loop) is None

    def test_cycle_does_not_hide_a_real_ref(self):
        """Cycle detection must not short-circuit a sibling branch that
        does hold a Ref."""
        s = src(x=1)
        loop: dict = {}
        loop["self"] = loop
        loop["other"] = {"buried": s["b"]}
        found = _find_nested_ref(loop)
        assert found is not None
        assert found[1].var == "b"

    def test_shared_subtree_is_not_a_cycle(self):
        """The same dict reachable by two paths must not make the second
        path a false negative when the first had no Ref."""
        s = src(x=1)
        shared = {"plain": 1}
        found = _find_nested_ref({"a": shared, "b": shared, "c": s["a"]})
        assert found is not None
        assert found[1].var == "a"


class TestTransformedRefsStillCaught:
    """Every operator in ref.py returns a new Ref, so transformed and
    stream-policy refs must be caught nested and preserved top-level."""

    @pytest.mark.parametrize(
        "make",
        [
            pytest.param(lambda s: s["a"] + 1, id="arithmetic"),
            pytest.param(lambda s: s["a"] >= 5, id="comparison"),
            pytest.param(lambda s: s["a"]["k"], id="getitem"),
            pytest.param(lambda s: s["a"].upper, id="getattr"),
            pytest.param(lambda s: s["a"].parallel(max=4), id="parallel"),
            pytest.param(lambda s: s["a"].collect(), id="collect"),
        ],
    )
    def test_nested_is_rejected(self, make):
        s = src(x=1)
        with pytest.raises(TypeError, match=r"nested inside a dict"):
            sink(v={"k": make(s)})

    @pytest.mark.parametrize(
        "make",
        [
            pytest.param(lambda s: s["a"] + 1, id="arithmetic"),
            pytest.param(lambda s: s["a"] >= 5, id="comparison"),
            pytest.param(lambda s: s["a"].parallel(max=4), id="parallel"),
            pytest.param(lambda s: s["a"].collect(), id="collect"),
        ],
    )
    def test_top_level_is_untouched(self, make):
        s = src(x=1)
        node = sink(v=make(s))
        assert isinstance(node.inputs["v"].value, Ref)

    def test_stream_policy_survives(self):
        """`.parallel()` / `.collect()` set attrs that resolve_value
        copies onto the rebuilt Ref — the check must not disturb that."""
        s = src(x=1)
        node = sink(v=s["a"].parallel(max=4))
        ref = node.inputs["v"].value
        assert ref._stream_parallel is True
        assert ref._stream_parallel_max == 4


def test_normalize_params_surfaces_the_error():
    """The check lives in resolve_value, so it fires through the normal
    normalize_params entry point rather than only via op constructors."""
    s = src(x=1)
    with pytest.raises(TypeError, match=r"nested inside a dict"):
        normalize_params({"v": {"one": s["a"]}}, parent=None)
