"""The run journal and resume (RUNTIME_R3_PLAN §5): the journals' contract,
a resume after a real SIGKILL, and the refusals."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from operonx import END, START, Operon, graph, op
from operonx.durable import (
    END as STEP_END,
)
from operonx.durable import (
    JournalError,
    MemoryJournal,
    NonDeterministicResume,
    RunHeader,
    SqliteJournal,
    Step,
)

pytestmark = pytest.mark.unit


# ── the journals' contract ────────────────────────────────────────────────


@pytest.fixture(params=["memory", "sqlite"])
def journal(request, tmp_path):
    if request.param == "memory":
        return MemoryJournal()
    return SqliteJournal(tmp_path / "runs.db")


def test_a_journal_keeps_steps_in_order_and_values_exact(journal):
    journal.open_run(RunHeader("r1", "fp", inputs={"x": (1, 2)}, thread_id="t"))
    journal.append("r1", [Step("g.a", ("main",), 0, [("g.a", "y", ("main",), (3, 4))])])
    journal.append("r1", [Step("g.a", ("main",), STEP_END, status="ok"), Step("g.b", ("main",), 0)])

    header, steps = journal.read("r1")
    assert header.inputs == {"x": (1, 2)} and header.thread_id == "t"
    assert [(s.op, s.index) for s in steps] == [("g.a", 0), ("g.a", STEP_END), ("g.b", 0)]
    assert steps[0].writes[0][3] == (3, 4)  # a tuple stays a tuple


def test_a_journal_tracks_status_and_lists_runs(journal):
    journal.open_run(RunHeader("r1", "fp"))
    journal.open_run(RunHeader("r2", "fp"))
    journal.set_status("r1", "ok")

    assert [h.run_id for h in journal.runs("running")] == ["r2"]
    assert {h.run_id for h in journal.runs()} == {"r1", "r2"}
    with pytest.raises(JournalError, match="already in the journal"):
        journal.open_run(RunHeader("r1", "fp"))
    with pytest.raises(JournalError, match="no run 'nope'"):
        journal.read("nope")
    with pytest.raises(JournalError, match="run status is one of"):
        journal.set_status("r1", "done")


def test_two_sqlite_journals_on_one_file_see_each_others_runs(tmp_path):
    a, b = SqliteJournal(tmp_path / "j.db"), SqliteJournal(tmp_path / "j.db")
    a.open_run(RunHeader("r", "fp"))
    a.append("r", [Step("g.a", ("main",), 0)])
    b.append("r", [Step("g.b", ("main",), 0)])

    assert [s.op for s in b.read("r")[1]] == ["g.a", "g.b"]


# ── resume ────────────────────────────────────────────────────────────────

CALLS: list = []


@op
async def first(x: int) -> dict:
    CALLS.append("first")
    return {"y": x + 1}


@op
async def second(y: int) -> dict:
    CALLS.append("second")
    return {"z": y * 10}


@graph
def chain(x):
    a = first(x=x)
    b = second(y=a["y"])
    START >> a >> b >> END


class _Stop(BaseException):
    pass


class _StopAfter(MemoryJournal):
    def __init__(self, after):
        super().__init__()
        self.after, self.taken = after, 0

    def append(self, run_id, steps):
        for s in steps:
            if self.taken >= self.after:
                raise _Stop()
            super().append(run_id, [s])
            self.taken += 1


def _resume(engine, run_id="r", **kw):
    async def go():
        return await (await engine.resume(run_id, **kw)).result()

    return asyncio.run(go())


async def _stopped(engine, inputs, run_id="r"):
    try:
        await engine.run(inputs, run_id=run_id)
    except BaseException:  # noqa: BLE001 — the stop
        pass


def test_a_resumed_run_does_not_run_again_what_ended():
    CALLS.clear()
    journal = _StopAfter(1)  # `first` ends; `second` is never recorded
    engine = Operon(chain, params={"x": None}, journal=journal, durability="sync")
    asyncio.run(_stopped(engine, {"x": 1}))
    assert CALLS == ["first", "second"]

    journal.after = 10**9
    CALLS.clear()

    async def go():
        handle = await engine.resume("r")
        await handle.collect()  # the run has closed its journal
        return await handle.result()

    out = asyncio.run(go())
    assert out["z"] == 20 and CALLS == ["second"]
    assert [h.status for h in journal.runs()] == ["ok"]


def test_resume_refuses_a_changed_graph():
    journal = MemoryJournal()
    asyncio.run(Operon(chain, params={"x": None}, journal=journal).run({"x": 1}, run_id="r"))

    @graph
    def rewired(x):  # the run's graph, changed: one op fewer
        a = first(x=x)
        START >> a >> END

    other = Operon(rewired, params={"x": None}, journal=journal)
    with pytest.raises(JournalError, match="this engine's graph is"):
        _resume(other)
    assert _resume(other, allow_graph_change=True) is not None


def test_an_unjournalable_value_names_the_op_and_var():
    @op
    async def opens(x: int) -> dict:
        return {"handle": lambda: x}  # a lambda does not pickle

    @graph
    def g(x):
        o = opens(x=x)
        START >> o >> END

    engine = Operon(g, params={"x": None}, journal=MemoryJournal(), durability="sync")
    with pytest.raises(JournalError, match=r"\.o\.handle wrote a function"):
        asyncio.run(engine.run({"x": 1}, run_id="r"))


@pytest.mark.parametrize("durability", ["async", "exit"])
def test_every_durability_resumes_to_the_same_result(durability):
    CALLS.clear()
    journal = MemoryJournal()
    engine = Operon(chain, params={"x": None}, journal=journal, durability=durability)
    first_out = asyncio.run(engine.run({"x": 2}, run_id="r"))
    CALLS.clear()
    again = _resume(engine)

    assert again["z"] == first_out["z"] == 30
    assert CALLS == []  # everything had ended: replayed, nothing run


def test_a_generator_that_yields_otherwise_on_resume_is_refused():
    seen = {"n": 0}

    @op
    async def drift(x: int):
        seen["n"] += 1
        yield {"v": x + seen["n"]}  # differs on the second run
        yield {"v": 0}

    @op
    async def sink(v: int) -> dict:
        return {"w": v}

    @graph
    def g(x):
        d = drift(x=x)
        s = sink(v=d["v"])
        START >> d >> s >> END

    journal = _StopAfter(1)  # the first yield is recorded, the generator never ends
    engine = Operon(g, params={"x": None}, journal=journal, durability="sync")
    asyncio.run(_stopped(engine, {"x": 1}))
    journal.after = 10**9
    with pytest.raises(NonDeterministicResume, match="yield 0 differs"):
        _resume(engine)

    strict = Operon(g, params={"x": None}, journal=journal, on_resume="fail")
    with pytest.raises(NonDeterministicResume, match="on_resume='fail'"):
        _resume(strict)


def test_without_a_journal_nothing_is_recorded():
    engine = Operon(chain, params={"x": None})

    async def go():
        handle = engine.start({"x": 1})
        await handle.result()
        return handle

    assert asyncio.run(go()).state._durable is None
    with pytest.raises(RuntimeError, match="no journal="):
        _resume(engine)


# ── a real crash ─────────────────────────────────────────────────────────

_CHILD = """
import asyncio, os, sys
sys.path.insert(0, {root!r})
from operonx import END, START, Operon, graph, op
from operonx.durable import SqliteJournal

LOG = {log!r}

def note(name):
    with open(LOG, "a") as f:
        f.write(name + "\\n")

@op
async def charge(order: int) -> dict:
    note("charge")
    return {{"receipt": f"R-{{order}}"}}

@op
async def ship(receipt: str) -> dict:
    note("ship")
    if os.environ.get("HANG"):
        print("READY", flush=True)
        await asyncio.sleep(600)
    return {{"done": receipt + ":shipped"}}

@graph
def order(order):
    c = charge(order=order)
    s = ship(receipt=c["receipt"])
    START >> c >> s >> END

engine = Operon(order, params={{"order": None}}, journal=SqliteJournal({db!r}), durability="sync")
if sys.argv[1] == "start":
    asyncio.run(engine.run({{"order": 42}}, run_id="order-42"))
else:
    async def resume():
        return await (await engine.resume("order-42")).result()

    out = asyncio.run(resume())
    print("RESULT", out["done"], flush=True)
"""


def _wait_ready(proc):
    """Skip what the child logs to stdout (a leaked ``LOG_LEVEL=INFO`` logs
    there) up to its READY line; its stderr is read only once it has exited,
    since reading a live child's stderr waits for it forever."""
    for line in proc.stdout:
        if line.strip() == "READY":
            return
    proc.wait(10)
    pytest.fail(f"the child exited before READY:\n{proc.stderr.read()}")


def test_a_run_killed_mid_way_resumes_in_another_process(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    log = tmp_path / "calls.log"
    script = tmp_path / "child.py"
    script.write_text(
        textwrap.dedent(_CHILD.format(root=root, log=str(log), db=str(tmp_path / "j.db")))
    )

    proc = subprocess.Popen(
        [sys.executable, str(script), "start"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "HANG": "1"},
    )
    try:
        _wait_ready(proc)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(10)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == -signal.SIGKILL
    assert log.read_text().split() == ["charge", "ship"]
    assert [h.status for h in SqliteJournal(tmp_path / "j.db").runs()] == ["running"]

    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, str(script), "resume"], capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    assert "RESULT R-42:shipped" in done.stdout
    # charged once across both processes; the shipping that was cut off ran again
    assert log.read_text().split() == ["charge", "ship", "ship"]
    assert time.monotonic() - started < 30


def test_an_interrupt_an_op_yielded_is_replayed_on_resume():
    from operonx.core import Interrupt

    @op
    async def stops(x: int):
        yield {"y": x}
        yield Interrupt(ctx_to_cancel=("main", "[9]"), reason="nothing there")

    @op
    def stops_self(y: int):
        return Interrupt(reason="me")  # Interrupt.SELF: resolved on replay too

    @graph
    def g(x):
        s = stops(x=x)
        m = stops_self(y=s["y"])
        START >> s >> m >> END

    journal = MemoryJournal()
    engine = Operon(g, params={"x": None}, journal=journal, durability="sync")

    async def first():
        handle = engine.start({"x": 1}, run_id="r")
        await handle.collect()
        return handle.interrupts

    async def again():
        handle = await engine.resume("r")
        await handle.collect()
        return handle.interrupts

    seen = asyncio.run(first())
    assert len(seen) == 2
    assert asyncio.run(again()) == seen  # replayed, not lost
