"""A branch with no ``.else_()`` runs nothing when no case matches (E2).

``.build()`` closes a branch without a default, so when every case is
false the branch reports ``target=None``. The scheduler read a falsy
target as "not a branch" and fired every outgoing edge: the one call on
which nothing should have run ran every arm.
"""

import pytest

from operonx.core import Operon
from operonx.core.ops.base import END, START
from operonx.core.ops.flow.branch_op import if_
from operonx.core.ops.graph.graph_op import GraphOp
from operonx.core.ops.transform.func_op import op


@op
def read(n: int = 0) -> dict:
    return {"n": n}


@op
def high() -> dict:
    return {"high": True}


@op
def mid() -> dict:
    return {"mid": True}


@op
def after(h: bool = None, m: bool = None) -> dict:
    return {"after": True}


OUTPUT = {"h": "high", "m": "mid", "a": "after"}


def _ran(out, name) -> bool:
    return out["$state"][f"g.{name}", OUTPUT[name], None] is True


def _graph(n):
    with GraphOp(name="g") as g:
        r = read(n=n)
        h = high()
        m = mid()
        a = after(h=h["high"], m=m["mid"])
        START >> r >> if_(r["n"] > 100, h).if_(r["n"] > 50, m).build()
        r >> END
        h >> a
        m >> a
        a >> END
    return g


class TestNoCaseMatches:
    async def test_no_arm_runs(self):
        out = await Operon(_graph(1)).run(inputs={})
        assert not _ran(out, "h")
        assert not _ran(out, "m")

    async def test_an_op_fed_only_by_the_arms_does_not_run(self):
        out = await Operon(_graph(1)).run(inputs={})
        assert not _ran(out, "a")
        assert "after" not in out

    async def test_the_run_still_completes(self):
        out = await Operon(_graph(1)).run(inputs={})
        assert out["n"] == 1

    async def test_the_branch_reports_no_target(self):
        out = await Operon(_graph(1)).run(inputs={})
        state = out["$state"]
        assert state["g.route_1", "target", None] is None


class TestACaseMatches:
    @pytest.mark.parametrize("n,ran,skipped", [(500, "h", "m"), (70, "m", "h")])
    async def test_only_that_arm_runs(self, n, ran, skipped):
        out = await Operon(_graph(n)).run(inputs={})
        assert _ran(out, ran)
        assert not _ran(out, skipped)
        assert out["after"] is True
