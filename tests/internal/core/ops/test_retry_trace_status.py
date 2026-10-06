"""A superseded attempt is not a failure of the run.

An op that fails once and then succeeds under ``retry=`` left a trace node
with ``status="error"`` for its first attempt. The run's result was clean
and ``handle.errors`` empty, yet ``WorkflowTrace.status`` said ``"error"``,
a job marked the item ``failed`` (``f: ConnectionError: blip``), and a run
store counted an error — every reader of node status disagreed with the run.
A retried attempt now has ``status="retried"``, keeping its error text.
"""

import json

from operonx import END, START, Operon, Retry, graph, op
from operonx.app.jobs import ITEM_FAILED, ITEM_OK, Job
from operonx.core.workflow_trace import STATUS_ERROR, STATUS_OK, STATUS_RETRIED
from operonx.telemetry.consumers.local import LocalConsumer
from operonx.telemetry.runs.model import meta_of_trace, rows_of_trace, summarize

CALLS = {"n": 0}


@op(bound="sync", retry=Retry(max_attempts=2, initial=0.01, on=(ConnectionError,)))
def flaky(text: str = "") -> dict:
    CALLS["n"] += 1
    if CALLS["n"] % 2 == 1:
        raise ConnectionError("blip")
    return {"reply": text.upper()}


@graph
def once(text: str = ""):
    f = flaky(text=text)
    START >> f >> END


@op(bound="sync", retry=Retry(max_attempts=3, initial=0.01, on=(ConnectionError,)))
def always(text: str = "") -> dict:
    raise ConnectionError("down")
    return {"reply": text}  # never reached; names the output


@graph
def never(text: str = ""):
    a = always(text=text)
    START >> a >> END


def _summary(trace):
    rows = rows_of_trace(trace, LocalConsumer())  # what every run store summarizes
    summary, _rollups = summarize(trace.trace_id, rows, meta_of_trace(trace))
    return summary


async def test_a_retried_then_successful_op_leaves_an_ok_run(tmp_path):
    CALLS["n"] = 0
    handle = Operon(once, params={"text": None}, trace=[]).start({"text": "hi"})
    out = await handle.collect(unwrap=True)
    assert out == {"reply": "HI"} and handle.errors == {}

    trace = handle.trace
    assert [(n.op_id.rsplit(".", 1)[-1], n.status) for n in trace.nodes] == [
        ("f#main@1", STATUS_RETRIED),
        ("f#main", STATUS_OK),
    ]
    assert "ConnectionError: blip" in trace.nodes[0].error  # kept for debugging
    assert trace.status == "ok"
    assert [n.status for n in trace.leaves()] == [STATUS_OK]  # not a terminal step
    assert meta_of_trace(trace)["status"] == "ok"
    summary = _summary(trace)
    assert summary.status == "ok" and summary.errors == 0 and summary.first_error is None

    (tmp_path / "src.jsonl").write_text(json.dumps("hi") + "\n")
    job = Job(
        "j",
        graph=once,
        items=str(tmp_path / "src.jsonl"),
        input="text",
        record_dir=tmp_path / "rec",
        trace=[],
    )
    run = await job.run()
    assert [(i.status, i.error) for i in run.items] == [(ITEM_OK, None)]


async def test_an_op_that_never_succeeds_keeps_its_error(tmp_path):
    handle = Operon(never, params={"text": None}, trace=[]).start({"text": "hi"})
    await handle.collect(unwrap=True)

    trace = handle.trace
    assert [n.status for n in trace.nodes] == [STATUS_RETRIED, STATUS_RETRIED, STATUS_ERROR]
    assert trace.status == "error"
    summary = _summary(trace)
    assert summary.status == "error" and summary.errors == 1
    assert "down" in summary.first_error

    (tmp_path / "src.jsonl").write_text(json.dumps("hi") + "\n")
    job = Job(
        "j",
        graph=never,
        items=str(tmp_path / "src.jsonl"),
        input="text",
        record_dir=tmp_path / "rec",
        trace=[],
    )
    run = await job.run()
    assert run.items[0].status == ITEM_FAILED and "down" in run.items[0].error


# -- R2: a retried attempt's child executions are superseded with it -----------------

from operonx import child  # noqa: E402

STEPS = {"n": 0}


@op(retry=Retry(max_attempts=2, initial=0.01, on=(ConnectionError,)))
async def agent_step(text: str = "") -> dict:
    STEPS["n"] += 1
    async with child("model", inputs={"text": text}, op_type="llm") as m:
        m.outputs = {"content": "ok"}
    if STEPS["n"] == 1:
        async with child("tool", inputs={"q": text}, op_type="tool"):
            raise ConnectionError("the tool's backend blipped")
    return {"reply": text}


@graph
def agent_once(text: str = ""):
    a = agent_step(text=text)
    START >> a >> END


async def test_a_retried_attempts_failed_child_is_not_a_failure_of_the_run():
    STEPS["n"] = 0
    handle = Operon(agent_once, params={"text": None}, trace=[]).start({"text": "hi"})
    out = await handle.collect(unwrap=True)
    assert out == {"reply": "hi"} and handle.errors == {}
    trace = handle.trace
    (tool,) = [n for n in trace.nodes if n.op_name == "tool"]
    assert tool.status == STATUS_ERROR and tool.attempt == 1  # what happened, kept
    assert trace.status == "ok", "the attempt it belonged to was run again"
    summary = _summary(trace)
    assert summary.status == "ok" and summary.errors == 0 and summary.first_error is None
