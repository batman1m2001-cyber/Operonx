"""A run is named after its graph, not a neighbouring variable (C13, F11).

`auto_name` read an assignment target off the source lines *above* the
call when the bytecode showed none, so every service run was ``engine``
(the serve layer's local), every job run ``params`` (the line above
`Operon(...)` in `Job.engine`) and a script run ``out``.
"""

from operonx.app.jobs import Job
from operonx.app.serve.app import compile_graph
from operonx.core import END, START, Operon, graph, op
from operonx.telemetry.consumer import Consumer


@op
def double(x: int = 0) -> dict:
    return {"result": x * 2}


@graph
def enrich_one(val):
    d = double(x=val)
    START >> d >> END


class _Capture(Consumer):
    def __init__(self):
        super().__init__()
        self.traces = []

    def consume(self, trace):
        self.traces.append(trace)


class _Holder:
    pass


def test_no_assignment_falls_back_to_the_graph_function_name():
    params = {"val": None}  # the line above used to name the graph
    holder = _Holder()
    holder.engine = Operon(enrich_one, params=params)
    assert holder.engine.name == "enrich_one"


async def test_a_script_run_is_named_after_its_graph_not_its_result():
    cap = _Capture()
    out = await Operon(enrich_one, params={"val": None}, trace=[cap]).run(inputs={"val": 2})
    assert out["result"] == 4
    assert cap.traces[0].workflow_name == "enrich_one"


async def test_a_graph_assigned_to_a_variable_keeps_that_name():
    g = enrich_one(val=None)
    cap = _Capture()
    out = await Operon(g, trace=[cap]).run(inputs={"val": 2})
    assert out["result"] == 4
    assert cap.traces[0].workflow_name == "g"


async def test_a_job_trace_is_named_after_its_graph(tmp_path):
    cap = _Capture()
    job = Job(
        "enrich",
        graph=enrich_one,
        items=[1, 2],
        input="val",
        record_dir=tmp_path / "jobs",
        trace=[cap],
    )
    await job.run()
    assert {t.workflow_name for t in cap.traces} == {"enrich_one"}


def test_a_served_graph_is_named_after_its_graph():
    engine = compile_graph(f"{__name__}:enrich_one")
    assert engine.name == "enrich_one"
