"""A durable run that stops on purpose (RUNTIME_R3_PLAN D7, D8, D11): an
interrupt parks it until a resume answers, a drain stops it for a deploy,
and a graph with a door is not resumable."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap

import pytest

from operonx import END, START, InterruptOp, Operon, graph, op
from operonx.app.serve import ingress
from operonx.durable import JournalError, MemoryJournal, SqliteJournal

pytestmark = pytest.mark.unit

CALLS: list = []


@op
async def plan(x: int) -> dict:
    CALLS.append("plan")
    return {"plan": f"plan-{x}"}


@op
async def side(x: int) -> dict:
    CALLS.append("side")
    await asyncio.sleep(0.05)  # in flight when the interrupt parks
    return {"noted": x}


@op
async def act(response: str, plan: str) -> dict:
    CALLS.append("act")
    return {"done": f"{plan}:{response}"}


@graph
def approval(x):
    p = plan(x=x)
    s = side(x=x)
    ask = InterruptOp(payload=p["plan"])
    a = act(response=ask["response"], plan=p["plan"])
    START >> p >> ask >> a >> END
    START >> s >> END


def _engine(journal):
    return Operon(approval, params={"x": None}, journal=journal, durability="sync")


async def _start(engine, run_id="r"):
    handle = engine.start({"x": 7}, run_id=run_id)
    return handle, await handle.result()


async def _answer(engine, answers, run_id="r"):
    handle = await engine.resume(run_id, answers=answers)
    await handle.collect()  # the run has closed its journal
    return handle, await handle.result()


def test_an_interrupt_parks_the_run_until_a_resume_answers_it():
    CALLS.clear()
    journal = MemoryJournal()
    engine = _engine(journal)
    _, out = asyncio.run(_start(engine))

    [question] = out["$interrupted"]
    assert question["payload"] == "plan-7" and question["op"].endswith(".ask")
    assert "done" not in out
    assert sorted(CALLS) == ["plan", "side"]  # the op in flight finished
    assert [h.status for h in journal.runs()] == ["interrupted"]

    CALLS.clear()
    _, out = asyncio.run(_answer(engine, {question["interrupt_id"]: "yes"}))
    assert out["done"] == "plan-7:yes" and "$interrupted" not in out
    assert CALLS == ["act"]  # nothing that ended ran again
    assert [h.status for h in journal.runs()] == ["ok"]


def test_a_resume_without_the_answer_parks_again_on_the_same_question():
    journal = MemoryJournal()
    engine = _engine(journal)
    _, first = asyncio.run(_start(engine))
    _, again = asyncio.run(_answer(engine, {}))

    assert again["$interrupted"] == first["$interrupted"]
    assert [h.status for h in journal.runs()] == ["interrupted"]


def test_an_answer_to_an_unknown_question_is_refused():
    journal = MemoryJournal()
    engine = _engine(journal)
    asyncio.run(_start(engine))
    with pytest.raises(JournalError, match="no question 'nope'"):
        asyncio.run(_answer(engine, {"nope": 1}))


@op
async def slow(i: int) -> dict:
    CALLS.append(f"slow{i}")
    await asyncio.sleep(0.02)
    return {"i": i + 1}


@graph
def chain(i):
    a = slow(i=i)
    b = slow(i=a["i"])
    c = slow(i=b["i"])
    START >> a >> b >> c >> END


def test_drain_then_resume_ends_as_the_run_that_never_stopped():
    expected = asyncio.run(Operon(chain, params={"i": None}).run({"i": 0}))

    CALLS.clear()
    journal = MemoryJournal()
    engine = Operon(chain, params={"i": None}, journal=journal, durability="sync")

    async def drained():
        handle = engine.start({"i": 0}, run_id="d")
        await asyncio.sleep(0.01)  # the first op is in flight
        await handle.drain()
        return await handle.result()

    out = asyncio.run(drained())
    assert out.get("$drained") is True and "i" not in out
    assert CALLS == ["slow0"]  # in flight: finished; nothing new dispatched
    assert [h.status for h in journal.runs()] == ["drained"]

    CALLS.clear()

    async def resumed():
        return await (await engine.resume("d")).result()

    assert asyncio.run(resumed())["i"] == expected["i"] == 3
    assert CALLS == ["slow1", "slow2"]


def test_drain_needs_a_journal():
    async def go():
        handle = Operon(approval, params={"x": None}).start({"x": 1})
        try:
            await handle.drain()
        finally:
            handle.cancel()

    with pytest.raises(RuntimeError, match="journal="):
        asyncio.run(go())


@op
async def echo(item: str) -> dict:
    return {"out": item}


@graph
def served():
    door = ingress()
    e = echo(item=door["item"])
    START >> door >> e >> END


def test_a_graph_with_a_door_is_journalled_but_not_resumed():
    journal = MemoryJournal()
    engine = Operon(served, journal=journal)
    with pytest.raises(JournalError, match=r"door .*ingress"):
        asyncio.run(engine.resume("any"))


_CHILD = """
import asyncio, sys
sys.path.insert(0, {root!r})
from operonx import END, START, InterruptOp, Operon, graph, op
from operonx.durable import SqliteJournal

LOG = {log!r}

def note(name):
    with open(LOG, "a") as f:
        f.write(name + "\\n")

@op
async def charge(order: int) -> dict:
    note("charge")
    return {{"amount": order * 10}}

@op
async def refund(response: str, amount: int) -> dict:
    note("refund")
    return {{"refunded": amount if response == "approve" else 0}}

@graph
def refunds(order):
    c = charge(order=order)
    ask = InterruptOp(payload=c["amount"])
    r = refund(response=ask["response"], amount=c["amount"])
    START >> c >> ask >> r >> END

engine = Operon(refunds, params={{"order": None}}, journal=SqliteJournal({db!r}))

async def main():
    if sys.argv[1] == "start":
        out = await engine.run({{"order": 4}}, run_id="refund-4")
        [q] = out["$interrupted"]
        print("ASK", q["interrupt_id"], q["payload"], flush=True)
    else:
        handle = await engine.resume("refund-4", answers={{sys.argv[2]: "approve"}})
        print("RESULT", (await handle.result())["refunded"], flush=True)

asyncio.run(main())
"""


def _lines(proc, tag):
    """The child's *tag* line; what it logs to stdout before it is skipped."""
    lines = [line.split() for line in proc.stdout.splitlines() if line.startswith(tag)]
    assert len(lines) == 1, proc.stdout + proc.stderr
    return lines[0]


def test_an_interrupt_is_answered_in_another_process(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    log = tmp_path / "calls.log"
    script = tmp_path / "child.py"
    script.write_text(
        textwrap.dedent(_CHILD.format(root=root, log=str(log), db=str(tmp_path / "j.db")))
    )

    def run(*args):
        return subprocess.run(
            [sys.executable, str(script), *args], capture_output=True, text=True, timeout=60
        )

    asked = run("start")
    assert asked.returncode == 0, asked.stderr
    _, interrupt_id, payload = _lines(asked, "ASK")
    assert payload == "40"
    assert [h.status for h in SqliteJournal(tmp_path / "j.db").runs()] == ["interrupted"]

    answered = run("resume", interrupt_id)
    assert answered.returncode == 0, answered.stderr
    assert _lines(answered, "RESULT") == ["RESULT", "40"]
    assert log.read_text().split() == ["charge", "refund"]  # charged once
