"""R2 trace format: child executions, ``attrs``, ``attempt`` and ``inputs_from``
survive every consumer, and a generator's records stop repeating its inputs.

* a later record of a generator invocation names the first one in
  ``inputs_from`` instead of repeating its inputs; ``get_run`` gives them back;
* rows written before these keys existed read unchanged;
* every store, Langfuse included, round-trips children, ``attrs`` and
  ``attempt``, and the tree built from what it returns nests the children.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from operonx import END, START, Operon, Retry, child, graph, op
from operonx.core.workflow_trace import OpExecution, UpstreamRef, WorkflowTrace
from operonx.telemetry.consumers.langfuse import LangfuseConsumer, build_tree
from operonx.telemetry.consumers.local import LocalConsumer
from operonx.telemetry.runs.files import FilesRunStore
from operonx.telemetry.runs.langfuse import records_of_langfuse_trace
from operonx.telemetry.runs.model import RunRecord, RunSummary, rows_of_trace
from tests.internal.telemetry._stores import BACKENDS, open_backend

PROMPT = "x" * 5000


@op
async def stream(prompt: str, n: int):
    for i in range(n):
        yield {"token": f"t{i}"}


@op
async def agent(prompt: str) -> dict:
    async with child("turn", inputs={"n": 0}, op_type="turn") as turn:
        async with child("model", inputs={"q": "hi"}, op_type="llm") as call:
            call.outputs = {"content": "ok"}
            call.attrs["gen_ai.operation.name"] = "chat"
        turn.outputs = {"done": True}
    return {"answer": "ok"}


TRIES: List[int] = []


@op(retry=Retry(max_attempts=2, initial=0.001, jitter=False))
async def flaky(prompt: str) -> dict:
    TRIES.append(1)
    if len(TRIES) % 2 == 1:
        raise ConnectionError("503")
    return {"ok": True}


@graph
def flow(prompt):
    s = stream(prompt=prompt, n=3)
    a = agent(prompt=prompt)
    f = flaky(prompt=prompt)
    START >> s >> END
    START >> a >> END
    START >> f >> END


async def _run(trace=None, trace_id="r2-format"):
    TRIES.clear()
    engine = Operon(flow, params={"prompt": None}, trace=trace)
    handle = engine.start({"prompt": PROMPT}, trace_id=trace_id)
    await handle.collect()  # returns once the run's consumers have run
    return handle


# -- the trace in memory and as rows ---------------------------------------------------


@pytest.mark.asyncio
async def test_yield_records_reference_inputs():
    handle = await _run()
    yields = [n for n in handle.trace.nodes if n.op_name == "s"]
    assert len(yields) == 3
    first, *rest = yields
    assert first.inputs_from is None and first.inputs["prompt"] == PROMPT
    assert all(n.inputs_from == first.op_id for n in rest)
    assert all(n.inputs is first.inputs for n in rest), "in memory they still read it"
    rows = {r["op_id"]: r for r in rows_of_trace(handle.trace, LocalConsumer())}
    assert rows[first.op_id]["inputs"]["prompt"] == PROMPT
    for n in rest:
        assert "inputs" not in rows[n.op_id]
        assert rows[n.op_id]["inputs_from"] == first.op_id


@op
async def breaks(prompt: str):
    yield {"token": "a"}
    raise RuntimeError("stream broke")


@graph
def broken(prompt):
    b = breaks(prompt=prompt)
    START >> b >> END


@pytest.mark.asyncio
async def test_failure_record_of_a_generator_references_inputs():
    handle = Operon(broken, params={"prompt": None}).start({"prompt": PROMPT})
    await handle.result()
    first, failed = [n for n in handle.trace.nodes if n.op_name == "b"]
    assert failed.status == "error" and failed.inputs_from == first.op_id


@pytest.mark.asyncio
async def test_local_run_is_smaller_and_reads_back_whole(tmp_path):
    store = FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    handle = await _run(trace=store)
    run_dir = store.run_dir(handle.trace.trace_id)
    raw = (run_dir / "nodes.jsonl").read_text()
    # the stream's first yield, the agent, and each attempt of flaky: 4, not 6
    assert raw.count(PROMPT) == 4
    rec = store.get_run(handle.trace.trace_id)
    stream_rows = [r for r in rec.nodes if r["op_name"] == "s"]
    assert [r["inputs"]["prompt"] for r in stream_rows] == [PROMPT] * 3
    view = (run_dir / "view.txt").read_text()
    assert view.count(f"(as {stream_rows[0]['op_id']})") == 2


def test_old_rows_without_inputs_from_read():
    rows = [
        {"op_id": "g.s#main.[0]", "inputs": {"p": 1}, "outputs": {}},
        {"op_id": "g.s#main.[1]", "inputs": {"p": 1}, "outputs": {}},
    ]
    rec = RunRecord(summary=RunSummary(trace_id="old"), nodes=[dict(r) for r in rows])
    assert rec.nodes == rows


# -- every store round-trips children, attrs and attempt -----------------------------


def _execs(rows: List[Dict[str, Any]]) -> List[OpExecution]:
    """Rows back to records, the way a reader that draws the tree does."""
    out = []
    for r in rows:
        start = float(r["start_time"])
        out.append(
            OpExecution(
                op_id=r["op_id"],
                op_name=r["op_name"],
                op_full_name=r["op_full_name"],
                ctx=tuple(r["ctx"]),
                start_time=start,
                end_time=start + float(r.get("duration_ms") or 0.0) / 1000.0,
                inputs=r.get("inputs") or {},
                outputs=r.get("outputs") or {},
                upstreams=[UpstreamRef(**u) for u in r.get("upstreams") or [] if "from_op_id" in u],
                status=r.get("status") or "ok",
                is_yield=bool(r.get("is_yield")),
                attempt=int(r.get("attempt") or 1),
                attrs=r.get("attrs") or {},
            )
        )
    return out


def _check_round_trip(rows: List[Dict[str, Any]], id_of=lambda op_id: op_id):
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_name.setdefault(r["op_name"], []).append(r)
    (model,) = by_name["model"]
    assert model["attrs"] == {"gen_ai.operation.name": "chat"}
    assert model["op_type"] == "llm" and model["outputs"] == {"content": "ok"}
    assert sorted(int(r.get("attempt") or 1) for r in by_name["f"]) == [1, 2]
    streams = by_name["s"]
    assert [r["inputs"]["prompt"] for r in streams] == [PROMPT] * 3
    assert sum("inputs_from" in r for r in streams) == 2
    trace = WorkflowTrace(trace_id="t", workflow_name="flow", started_at=0.0, ended_at=0.0)
    trace.nodes = _execs(rows)
    tree = build_tree(trace)
    (turn,) = by_name["turn"]
    (op_rec,) = by_name["a"]
    assert tree[model["op_id"]]["parent"] == turn["op_id"]
    assert tree[turn["op_id"]]["parent"] == op_rec["op_id"]


@pytest.mark.parametrize("kind", BACKENDS)
@pytest.mark.asyncio
async def test_children_and_attrs_round_trip(kind, request, tmp_path):
    store = open_backend(kind, request, tmp_path)
    handle = await _run(trace=store, trace_id=f"rt-{kind}")
    rec = store.get_run(handle.trace.trace_id)
    assert rec is not None
    _check_round_trip(rec.nodes)


@pytest.mark.asyncio
async def test_children_and_attrs_round_trip_langfuse():
    batches: List[List[Dict[str, Any]]] = []

    class Client:
        def ingest(self, batch, timeout: int = 30):
            batches.append(json.loads(json.dumps(batch, default=str)))
            return {}

        def trace_url(self, trace_id):
            return f"https://lf.test/{trace_id}"

    handle = await _run(trace=LangfuseConsumer(config={"client": Client()}))
    (batch,) = batches
    observations = [
        {
            "id": e["body"]["id"],
            "name": e["body"]["name"],
            "startTime": e["body"]["startTime"],
            "endTime": e["body"]["endTime"],
            "metadata": e["body"]["metadata"],
            "input": e["body"].get("input"),
            "output": e["body"].get("output"),
            "level": e["body"].get("level"),
        }
        for e in batch
        if e["type"] != "trace-create" and e["body"]["metadata"]["kind"] == "record"
    ]
    referencing = [o for o in observations if "inputs_from" in o["metadata"]]
    assert len(referencing) == 2 and all(o["input"] is None for o in referencing)
    parent = {e["body"]["name"]: e["body"]["parentObservationId"] for e in batch[1:]}
    ids = {e["body"]["name"]: e["body"]["id"] for e in batch[1:]}
    assert parent["model"] == ids["turn"] and parent["turn"] == ids["a"]
    rows = list(records_of_langfuse_trace({"observations": observations}))
    for r in rows:  # Langfuse ids are scoped "<run>/<op_id>"; the ctx is a string there
        r["ctx"] = r["ctx"].split(".")
    rec = RunRecord(summary=RunSummary(trace_id=handle.trace.trace_id), nodes=rows)
    by_op = {r["op_name"]: r for r in rec.nodes}
    assert by_op["model"]["attrs"] == {"gen_ai.operation.name": "chat"}
    # a yield observation is named "s [i]"
    streams = [r for r in rec.nodes if r["op_full_name"].endswith(".s")]
    assert [r["inputs"]["prompt"] for r in streams] == [PROMPT] * 3
    assert sorted(int(r.get("attempt") or 1) for r in rec.nodes if r["op_name"] == "f") == [1, 2]


# -- a child's redact= applies where the trace leaves the process ------------------------

SECRET = "sk-live-0123456789"


def _scrub(values: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(values).replace(SECRET, "[redacted]"))


@op
async def keyholder(prompt: str) -> dict:
    async with child("tool", inputs={"key": SECRET, "prompt": prompt}, op_type="tool") as call:
        call.outputs = {"message": f"the key is {SECRET}"}
        call.redact = _scrub
    return {"answer": "ok"}


@graph
def keyed(prompt):
    k = keyholder(prompt=prompt)
    START >> k >> END


async def _keyed(trace=None, trace_id="redact"):
    handle = Operon(keyed, params={"prompt": None}, trace=trace).start(
        {"prompt": "hi"}, trace_id=trace_id
    )
    await handle.collect()
    return handle


@pytest.mark.asyncio
async def test_redact_applies_on_export_and_the_record_in_memory_keeps_its_values():
    handle = await _keyed()
    (tool,) = [n for n in handle.trace.nodes if n.op_name == "tool"]
    assert tool.inputs["key"] == SECRET, "in memory, as recorded"
    assert tool.exported() == (
        {"key": "[redacted]", "prompt": "hi"},
        {"message": "the key is [redacted]"},
    )
    rows = {r["op_name"]: r for r in rows_of_trace(handle.trace, LocalConsumer())}
    assert SECRET not in json.dumps(rows["tool"]) and rows["tool"]["inputs"]["key"] == "[redacted]"


@pytest.mark.parametrize("kind", BACKENDS)
@pytest.mark.asyncio
async def test_redact_holds_in_every_store(kind, request, tmp_path):
    store = open_backend(kind, request, tmp_path)
    handle = await _keyed(trace=store, trace_id=f"redact-{kind}")
    rec = store.get_run(handle.trace.trace_id)
    (tool,) = [r for r in rec.nodes if r["op_name"] == "tool"]
    assert tool["inputs"] == {"key": "[redacted]", "prompt": "hi"}
    assert SECRET not in json.dumps(rec.nodes, default=str)


@pytest.mark.asyncio
async def test_redact_holds_in_the_local_view_and_langfuse(tmp_path):
    store = FilesRunStore(root=tmp_path / "runs", refresh_every=0)
    handle = await _keyed(trace=store)
    run_dir = store.run_dir(handle.trace.trace_id)
    for name in ("nodes.jsonl", "view.txt"):
        assert SECRET not in (run_dir / name).read_text(), name

    batches: List[List[Dict[str, Any]]] = []

    class Client:
        def ingest(self, batch, timeout: int = 30):
            batches.append(json.loads(json.dumps(batch, default=str)))
            return {}

        def trace_url(self, trace_id):
            return f"https://lf.test/{trace_id}"

    await _keyed(trace=LangfuseConsumer(config={"client": Client()}))
    (batch,) = batches
    assert SECRET not in json.dumps(batch)
    (tool,) = [e["body"] for e in batch if e["body"].get("name") == "tool"]
    assert tool["input"]["key"] == "[redacted]"
