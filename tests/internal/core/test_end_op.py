"""``END >> op`` — the op the runtime calls once after the run.

It replaces a Service's ``on_close`` hook: work that must happen however a
run ended (a call's record, a counter given back) as an op of the graph,
traced like any other. The contract (callbot plan, O4):

1. once, at the top level, after every other op has finished or been
   cancelled — on success, a recorded failure, fail-fast, cancellation;
2. it reads graph parameters, declared cells and SCRATCH, never another
   op's output;
3. nothing flows out of it: no edges, no cell writes, not a reply key;
4. shielded from cancellation, bounded by its own timeout (or 10 s);
5. its failure is its own: recorded as handled, the run's status stands.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from operonx.core import END, PARENT, SCRATCH, START, Operon, graph, op
from operonx.core.ops.flow.branch_op import if_
from operonx.core.policy import OpFailed, Timeout
from operonx.core.workflow_trace import unhandled

pytestmark = pytest.mark.unit

SEEN: list = []


@op
def add_one(n: int) -> dict:
    return {"n": n + 1}


@op
def boom(n: int) -> dict:
    raise ValueError("boom")


@op
async def stream(n: int):
    for i in range(n):
        await asyncio.sleep(0.01)
        yield {"i": i}


@op
def mark(i: int) -> dict:
    return {"marked": i}


@op
async def closing(total: int = 0, note: str = "") -> dict:
    SEEN.append({"total": total, "note": note, "scratch": SCRATCH.get("opened")})
    return {"closed": True}


@op
async def slow_closing(total: int = 0) -> dict:
    await asyncio.sleep(0.2)
    SEEN.append({"total": total, "slow": True})
    return {}


@op(timeout=Timeout(run=0.1))
async def hung_closing(total: int = 0) -> dict:
    await asyncio.sleep(5)
    SEEN.append({"hung": "finished"})
    return {}


@op
def broken_closing(total: int = 0) -> dict:
    raise RuntimeError("the end op itself failed")


@op
def opener(n: int) -> dict:
    SCRATCH["opened"] = f"call {n}"
    return {"n": n}


@op
async def forever(n: int):
    while True:
        await asyncio.sleep(0.01)
        yield {"i": n}


@graph
def counted(n):
    PARENT.declare(total=0)
    o = opener(n=n)
    a = add_one(n=o["n"])
    a["n"] >> PARENT["total"]
    c = closing(total=PARENT["total"], note=n)
    START >> o >> a >> END
    END >> c


@graph
def failing(n):
    PARENT.declare(total=0)
    a = boom(n=n)
    c = closing(total=PARENT["total"])
    START >> a >> END
    END >> c


@graph
def streaming(n):
    PARENT.declare(total=0)
    s = stream(n=n)
    m = mark(i=s["i"])
    m["marked"] >> PARENT["total"]
    c = closing(total=PARENT["total"])
    START >> s >> m >> END
    END >> c


@graph
def endless(n):
    PARENT.declare(total=0)
    f = forever(n=n)
    c = slow_closing(total=PARENT["total"])
    START >> f >> END
    END >> c


@graph
def hung(n):
    a = add_one(n=n)
    c = hung_closing()
    START >> a >> END
    END >> c


@graph
def broken(n):
    a = add_one(n=n)
    c = broken_closing()
    START >> a >> END
    END >> c


@op
def increment(counter: int) -> dict:
    return {"counter": counter + 1}


@graph
def looping():
    PARENT.declare(count=0)
    inc = increment(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]
    c = closing(total=PARENT["count"])
    START >> inc >> if_(PARENT["count"] >= 3, END).else_(inc)
    END >> c


@pytest.fixture(autouse=True)
def _fresh():
    SEEN.clear()


async def _run(g, **inputs):
    return await asyncio.wait_for(Operon(g).run(inputs=inputs), timeout=20)


class TestWhenItRuns:
    @pytest.mark.asyncio
    async def test_once_after_the_run_with_its_cells_and_scratch(self):
        out = await _run(counted(n=4))
        assert SEEN == [{"total": 5, "note": 4, "scratch": "call 4"}]
        # not a graph output: the answer is what the run made
        assert "closed" not in out

    @pytest.mark.asyncio
    async def test_after_a_recorded_failure(self):
        out = await _run(failing(n=1))
        assert len(SEEN) == 1
        assert any(k.endswith(".a") for k in out["$errors"])

    @pytest.mark.asyncio
    async def test_under_fail_fast(self):
        with pytest.raises(OpFailed):
            await asyncio.wait_for(Operon(failing(n=1), errors="raise").run(inputs={}), timeout=20)
        assert len(SEEN) == 1

    @pytest.mark.asyncio
    async def test_after_a_stream_ends_once(self):
        await _run(streaming(n=5))
        assert len(SEEN) == 1
        assert SEEN[0]["total"] == 4

    @pytest.mark.asyncio
    async def test_on_cancel_and_through_repeated_cancels(self):
        handle = Operon(endless(n=1)).start(inputs={})
        await asyncio.sleep(0.1)
        task = handle._scheduler_task
        for _ in range(3):
            handle.cancel()
            task.cancel()
            await asyncio.sleep(0.05)
        await asyncio.wait({task}, timeout=5)
        assert SEEN == [{"total": 0, "slow": True}]

    @pytest.mark.asyncio
    async def test_bounded_by_its_own_timeout(self):
        out = await _run(hung(n=1))
        assert out["n"] == 2
        assert SEEN == []

    @pytest.mark.asyncio
    async def test_its_failure_does_not_fail_the_run(self, caplog):
        out = await _run(broken(n=1))
        assert out["n"] == 2
        errors = out.get("$errors") or {}
        assert any(k.endswith(".c") for k in errors)
        assert unhandled(errors) == {}

    @pytest.mark.asyncio
    async def test_a_loop_in_the_graph_does_not_repeat_it(self):
        await _run(looping())
        assert len(SEEN) == 1
        assert SEEN[0]["total"] == 3

    @pytest.mark.asyncio
    async def test_no_orphan_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            counted(n=1).build()
        assert not [r for r in caplog.records if "never be executed" in r.getMessage()]
        assert not [r for r in caplog.records if "not reachable" in r.getMessage()]


@graph
def two_in_a_list(n):
    a = add_one(n=n)
    b = closing()
    c = closing()
    START >> a >> END
    END >> [b, c]


@graph
def two_after_end(n):
    a = add_one(n=n)
    b = closing()
    c = closing()
    START >> a >> END
    END >> b
    END >> c


@graph
def chained_after_an_op(n):
    a = add_one(n=n)
    b = closing()
    START >> a >> END >> b


@graph
def edge_out_of_it(n):
    a = add_one(n=n)
    b = closing()
    c = closing()
    START >> a >> END
    END >> b >> c


@graph
def soft_after_end(n):
    a = add_one(n=n)
    b = closing()
    START >> a >> END
    END > b


@graph
def also_wired(n):
    a = add_one(n=n)
    b = closing()
    START >> a >> b >> END
    END >> b


@graph
def reads_an_output(n):
    a = add_one(n=n)
    b = closing(total=a["n"])
    START >> a >> END
    END >> b


@graph
def writes_a_cell(n):
    PARENT.declare(total=0)
    a = add_one(n=n)
    b = closing()
    b["closed"] >> PARENT["total"]
    START >> a >> END
    END >> b


@graph
def outer(n):
    inner = counted(n=n)
    START >> inner >> END


class TestWhatIsRefused:
    def test_a_list(self):
        with pytest.raises(TypeError, match="one op runs after END"):
            two_in_a_list(n=1)

    def test_a_second_one(self):
        with pytest.raises(TypeError, match="already runs"):
            two_after_end(n=1)

    def test_chained_after_an_op(self):
        with pytest.raises(TypeError, match="its own line"):
            chained_after_an_op(n=1)

    def test_an_edge_out_of_it(self):
        with pytest.raises(TypeError, match="nothing runs after"):
            edge_out_of_it(n=1)

    def test_a_soft_edge(self):
        with pytest.raises(TypeError, match="END >> op"):
            soft_after_end(n=1)

    def test_other_edges_into_it(self):
        with pytest.raises(TypeError, match="has no edges"):
            also_wired(n=1).build()

    def test_reading_another_ops_output(self):
        with pytest.raises(Exception, match="runs after END"):
            reads_an_output(n=1).build()

    def test_writing_a_cell(self):
        with pytest.raises(TypeError, match="writes PARENT"):
            writes_a_cell(n=1).build()

    def test_inside_a_subgraph(self):
        with pytest.raises(TypeError, match="Move it to the root graph"):
            outer(n=1).build()
