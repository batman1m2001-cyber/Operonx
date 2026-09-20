"""show_keys: the outputs a viewer prints for an op.

Resolution, first hit wins: the instance (`show_keys=` at the call
site), the declaration (`@op(show_keys=...)`), the class default
(`show_keys_default`). Unset means empty — the viewer picks.
"""

import pytest

from operonx.core import END, PARENT, START, graph
from operonx.core.ops import op
from operonx.core.ops.base import BaseOp, _normalise_show
from operonx.core.ops.flow.branch_op import BranchOp


@op(show_keys="text")
def declared(x: int):
    return {"text": str(x), "n": x}


@op
def undeclared(text: str):
    return {"out": text}


class TestResolution:
    def test_declaration_is_the_default(self):
        node = declared()
        assert node.show_keys == ("text",)

    def test_call_site_overrides_declaration(self):
        node = declared(show_keys=["n", "text"])
        assert node.show_keys == ("n", "text")

    def test_unset_is_empty(self):
        node = undeclared()
        assert node.show_keys == ()
        assert BaseOp.show_keys_default == ()

    def test_call_site_on_an_undeclared_op(self):
        node = undeclared(show_keys="out")
        assert node.show_keys == ("out",)

    def test_class_default_when_instance_declares_none(self):
        class Custom(BaseOp):
            show_keys_default = ("answer",)

            def _process(self, **kw):  # pragma: no cover - never run
                return {}

        node = Custom()
        assert node.show_keys == ("answer",)
        node = Custom(show_keys="other")
        assert node.show_keys == ("other",)

    def test_branch_default_is_target(self):
        assert BranchOp.show_keys_default == ("target",)


class TestNormalisation:
    def test_string_becomes_one_key(self):
        assert _normalise_show("a") == ("a",)

    def test_sequence_becomes_tuple(self):
        assert _normalise_show(["a", "b"]) == ("a", "b")
        assert _normalise_show(("a",)) == ("a",)

    def test_none_is_empty(self):
        assert _normalise_show(None) == ()

    def test_non_string_entries_are_rejected(self):
        with pytest.raises(TypeError, match="must be str"):
            declared(show_keys=[1])
        with pytest.raises(TypeError, match="str or a sequence"):
            declared(show_keys=3)


class TestGraphCallSite:
    def test_graph_takes_show_keys_at_the_call_site(self):
        @graph
        def sub(x):
            n = declared(x=x)
            n["text"] >> PARENT["text"]
            START >> n >> END

        @graph
        def main(x):
            s = sub(x=x, show_keys="text")
            t = undeclared(text=s["text"])
            START >> s >> t >> END
            return {"out": t["out"]}

        g = main(x=1)
        assert g._ops["s"].show_keys == ("text",)
        assert g._ops["t"].show_keys == ()
        # the keyword never lands in the input mapping
        assert "show_keys" not in g._ops["s"].inputs
