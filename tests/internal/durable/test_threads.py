"""A thread carries declared cells between its runs (RUNTIME_R4_PLAN D7)."""

from __future__ import annotations

import asyncio
import operator
import os
import subprocess
import sys
import textwrap

import pytest

from operonx import END, PARENT, START, Operon, graph, op
from operonx.durable import MemoryJournal, SqliteJournal

pytestmark = pytest.mark.unit


@op
def reply(text: str, history: list) -> dict:
    return {"said": [text], "count": len(history) + 1}


@graph
def chat(text):
    PARENT.declare(history=[], reducers={"history": operator.add})
    r = reply(text=text, history=PARENT["history"])
    r["said"] >> PARENT["history"]
    START >> r >> END


def _history(out):
    """The cell as the run left it (a result shows the run's own write)."""
    state = out["$state"]
    return state[state.schema.name, "history"]


def _engine(journal):
    return Operon(chat, params={"text": None}, journal=journal, carry=["history"])


def test_a_thread_carries_cells_between_runs():
    engine = _engine(MemoryJournal())

    async def go():
        one = await engine.run({"text": "hi"}, thread_id="t1")
        two = await engine.run({"text": "again"}, thread_id="t1")
        other = await engine.run({"text": "new"}, thread_id="t2")
        alone = await engine.run({"text": "solo"})  # no thread: nothing carried
        return one, two, other, alone

    one, two, other, alone = (_history(out) for out in asyncio.run(go()))
    assert one == ["hi"]
    assert two == ["hi", "again"]  # the second run began with the first's
    assert other == ["new"] and alone == ["solo"]


def test_carry_names_declared_cells_and_needs_a_journal():
    with pytest.raises(ValueError, match="journal="):
        Operon(chat, params={"text": None}, carry=["history"])
    with pytest.raises(ValueError, match=r"\['nope'\] not declared"):
        Operon(chat, params={"text": None}, journal=MemoryJournal(), carry=["nope"])


def test_a_resumed_run_starts_from_what_its_thread_gave_it():
    journal = MemoryJournal()
    engine = _engine(journal)

    async def go():
        await engine.run({"text": "a"}, thread_id="t", run_id="r1")
        await engine.run({"text": "b"}, thread_id="t", run_id="r2")
        # the thread moved on since r2 began; resuming r2 must not see "b" twice
        handle = await engine.resume("r2")
        await handle.result()
        return handle.state[handle.state.schema.name, "history"]

    assert asyncio.run(go()) == ["a", "b"]


_CHILD = """
import asyncio, operator, sys
sys.path.insert(0, {root!r})
from operonx import END, PARENT, START, Operon, graph, op
from operonx.durable import SqliteJournal

@op
def reply(text: str, history: list) -> dict:
    return {{"said": [text], "count": len(history) + 1}}

@graph
def chat(text):
    PARENT.declare(history=[], reducers={{"history": operator.add}})
    r = reply(text=text, history=PARENT["history"])
    r["said"] >> PARENT["history"]
    START >> r >> END

engine = Operon(chat, params={{"text": None}}, journal=SqliteJournal({db!r}), carry=["history"])
out = asyncio.run(engine.run({{"text": sys.argv[1]}}, thread_id="cust-7"))
state = out["$state"]
print("COUNT", len(state[state.schema.name, "history"]), flush=True)
"""


def test_a_thread_carries_across_processes(tmp_path):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(_CHILD.format(root=root, db=str(tmp_path / "j.db"))))
    counts = []
    for text in ("one", "two", "three"):
        done = subprocess.run(
            [sys.executable, str(script), text], capture_output=True, text=True, timeout=60
        )
        assert done.returncode == 0, done.stderr
        counts += [line.split()[1] for line in done.stdout.splitlines() if line.startswith("COUNT")]
    assert counts == ["1", "2", "3"]
    assert SqliteJournal(tmp_path / "j.db").load_thread("cust-7")["history"] == [
        "one",
        "two",
        "three",
    ]
