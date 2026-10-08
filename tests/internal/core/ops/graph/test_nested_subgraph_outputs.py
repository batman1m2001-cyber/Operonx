"""A nested subgraph's output survives a failure elsewhere in the run.

When any op in a run records a failure, each subgraph drops the outputs no op
under it wrote (`GraphOp._drop_unwritten`), so a graph never hands on its own
input as an answer. It decided "wrote" from the writer's `error` cell — and a
subgraph never writes one. Two levels down, every output a subgraph wrote was
dropped: sentiment's l4 kept a verdict through a soften timeout
(`on_failure="error"`), and the call still came back with no result.
"""

import asyncio
from unittest.mock import patch

from operonx import END, START, graph, op
from operonx.core import Operon
from operonx.core.ops import if_
from operonx.providers.ops import LLMOp
from tests.internal.providers.test_extract_retry import make_mock_hub


@op
def gate(raw: dict = None) -> dict:
    return {"go": True, "text": "x"}


@op
def keep(raw: dict = None, soft: dict = None, error: str = None) -> dict:
    return {"result": {**(raw or {}), "soften": "failed" if error else "ok"}}


@op
def broken(raw: dict = None) -> dict:
    raise ValueError("boom")


@op
def pick(r: dict = None) -> dict:
    return {"result": r}


@op
def load(item: dict = None) -> dict:
    return {"conv": item}


@graph
def leaf(result: dict):
    g = gate(raw=result)
    s = LLMOp.of(
        resource="mock",
        prompt={"user": "{x}"},
        fields=["result: dict"],
        parser="json",
        x=g["text"],
        on_failure="error",
    )
    k = keep(raw=result, soft=s["result"], error=s["error"])
    START >> g >> if_(g["go"], s).else_(k)
    s >> k >> END


@graph
def mid(conv: dict):
    d = pick(r=conv)
    v = leaf(result=d["result"])
    START >> d >> v >> END


@graph
def top(item: dict):
    c = load(item=item)
    a = mid(conv=c["conv"])
    START >> c >> a >> END


@graph
def leaf_broken(result: dict):
    b = broken(raw=result)
    START >> b >> END


@graph
def mid_broken(conv: dict):
    d = pick(r=conv)
    v = leaf_broken(result=d["result"])
    START >> d >> v >> END


@graph
def top_broken(item: dict):
    c = load(item=item)
    a = mid_broken(conv=c["conv"])
    START >> c >> a >> END


async def _timeout(self, params):
    raise TimeoutError("exceeded 90s")


def _run(g):
    hub, _ = make_mock_hub(["unused"])
    with (
        patch("operonx.providers.ops._utils.ResourceHub") as m,
        patch.object(LLMOp, "_call_once", _timeout),
    ):
        m.instance.return_value = hub
        return asyncio.run(Operon(g, params={"item": None}).run({"item": {"v": 1}}))


def test_a_handled_failure_two_levels_down_keeps_the_output():
    out = _run(top)
    assert out["result"] == {"v": 1, "soften": "failed"}


def test_a_raise_two_levels_down_still_drops_the_input_passed_through():
    """The guard still holds: `leaf_broken` shares its input's cell for
    `result`, and its writer raised — the input must not come back as the
    answer."""
    out = _run(top_broken)
    assert out.get("result") is None
    assert "$errors" in out
