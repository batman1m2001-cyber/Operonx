"""A failure the graph handles does not fail the item, the request or the run.

`LLMOp(on_failure="error")` declares a step's output optional, and an error
edge (`op.on_error`) declares where a failure goes. Both were still read as
the run's failure: `"$errors"` carried the record, and a `Job` marked the
item `failed` from it — sentiment's soften step timed out, its verdict was
kept by the graph, and the call was written as `{"error": ...}` anyway.

The record stays (`handled: true`): handled is not hidden. Everything that
decides "failed" reads `unhandled(errors)` instead. A parse failure is not
handled — left unchecked it reads as "no" — and still fails the item.
"""

import asyncio
import json
from unittest.mock import patch

from operonx import END, START, graph, op
from operonx.app.jobs import Job
from operonx.core import PARENT, GraphOp, Operon
from operonx.core.workflow_trace import unhandled
from operonx.telemetry.consumers.local import LocalConsumer


@op
async def lookup(order: int) -> dict:
    if order < 0:
        raise ConnectionError("crm down")
    return {"status": f"order {order} shipped"}


@op
def apologise(error: str) -> dict:
    return {"status": "sorry, try later"}


@graph
def answer(order):
    look = lookup(order=order)
    sorry = apologise()
    START >> look >> END
    look.on_error(sorry)
    sorry >> END


@graph
def answer_unhandled(order):
    look = lookup(order=order)
    START >> look >> END


def _job(g, tmp_path, items):
    return Job("t", graph=g, items=items, key="order", record_dir=tmp_path, trace=[])


def test_an_error_edge_keeps_the_item_ok(tmp_path):
    run = asyncio.run(_job(answer, tmp_path, [{"order": -1}, {"order": 7}]).run())
    assert run.status == "ok" and run.counts.get("failed", 0) == 0
    assert {"status": "sorry, try later"} in run.results.values()


def test_an_unhandled_raise_still_fails_the_item(tmp_path):
    run = asyncio.run(_job(answer_unhandled, tmp_path, [{"order": -1}]).run())
    assert run.status == "failed" and run.counts["failed"] == 1


def test_the_handled_record_stays_on_the_run():
    out = asyncio.run(Operon(answer, params={"order": None}).run({"order": -1}))
    (record,) = out["$errors"].values()
    assert record["handled"] is True and record["type"] == "ConnectionError"
    assert unhandled(out["$errors"]) == {}


def _llm_graph(**kwargs):
    from operonx.providers.ops import LLMOp

    @op
    def carry_on(summary=None, error=None) -> dict:
        return {"result": {"summary": summary, "error": (error or "").split(":")[0] or None}}

    with GraphOp(name="chain") as g:
        ex = LLMOp.of(resource="mock", prompt={"user": "{text}"}, fields=["summary: str"],
                      parser="json", text=PARENT["text"], **kwargs)
        go = carry_on(summary=ex["summary"], error=ex["error"])
        START >> ex >> go >> END
    return g


def _run_job(g, tmp_path, responses):
    from tests.internal.providers.test_extract_retry import make_mock_hub

    mock_hub, _ = make_mock_hub(responses)
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        job = Job("t", graph=g, items=[{"text": "x"}], key="text", record_dir=tmp_path, trace=[])
        return asyncio.run(job.run())


def test_an_optional_llm_step_that_fails_keeps_the_item_ok(tmp_path):
    from operonx.providers.ops import LLMOp

    async def boom(self, params):
        raise TimeoutError("exceeded 90s after 2 attempts")

    with patch.object(LLMOp, "_call_once", boom):
        run = _run_job(_llm_graph(on_failure="error"), tmp_path, ["unused"])
    assert run.status == "ok" and run.counts.get("failed", 0) == 0
    assert run.results["x"] == {"result": {"summary": None, "error": "TimeoutError"}}


def test_a_parse_failure_is_not_handled_and_fails_the_item(tmp_path):
    run = _run_job(_llm_graph(max_retries=0), tmp_path, ["not json at all"])
    assert run.status == "failed" and run.counts["failed"] == 1


def test_a_trace_with_only_handled_failures_is_ok(tmp_path):
    engine = Operon(answer, params={"order": None}, trace=LocalConsumer(config={"root": str(tmp_path)}))
    asyncio.run(engine.run({"order": -1}))
    (meta_path,) = tmp_path.rglob("meta.json")
    meta = json.loads(meta_path.read_text())
    assert meta["status"] == "ok"
    assert all(r.get("handled") for r in meta["errors"].values())


def test_one_unhandled_failure_clears_handled():
    from operonx.core.states.state import MemoryState

    state = MemoryState.__new__(MemoryState)
    state._op_errors = {}
    state.record_op_error("g.a", "TimeoutError: x", handled=True)
    assert unhandled(state._op_errors) == {}
    state.record_op_error("g.a", "ValueError: y")
    assert list(unhandled(state._op_errors)) == ["g.a"] and state._op_errors["g.a"]["count"] == 2
