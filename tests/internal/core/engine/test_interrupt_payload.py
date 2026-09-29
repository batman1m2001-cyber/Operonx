"""The `__interrupt__` record is not part of a run's result (C3).

The scheduler reports a sweep as a synthetic `("__interrupt__", ctx,
{"__interrupt__": event})` frame on the same queue as real output frames.
`run()`, `collect()` and `result()` merged every frame's items, so the
tag arrived in the result as a key no graph declares, holding an object
`json.dumps` rejects. `handle.interrupts` is where it belongs.
"""

import json

from operonx.core import END, PARENT, START, GraphOp, Interrupt, Operon, op


@op
def echo(x: str):
    return {"echo": x}


@op
def stray(x: str):
    # Cancels a context nothing runs in: the record is all it produces.
    return Interrupt(ctx_to_cancel=("main", "[7]"), reason="c3")


def _graph():
    with GraphOp(name="c3") as g:
        e = echo(x=PARENT["x"])
        s = stray(x=PARENT["x"])
        START >> [e, s]
        [e, s] >> END
    return g


async def test_run_payload_has_only_the_graph_outputs():
    out = await Operon(_graph()).run(inputs={"x": "hi"})
    assert set(out) == {"echo", "$state"}
    json.dumps({k: v for k, v in out.items() if k != "$state"})


async def test_collect_and_result_leave_it_out_and_interrupts_keeps_it():
    engine = Operon(_graph())

    handle = engine.start(inputs={"x": "hi"})
    assert await handle.collect() == {"echo": ["hi"]}
    assert await handle.result() == {"echo": "hi"}
    assert [i.reason for i in handle.interrupts] == ["c3"]

    flat = await engine.start(inputs={"x": "hi"}).collect("flat")
    assert flat == [{"echo": "hi"}]
