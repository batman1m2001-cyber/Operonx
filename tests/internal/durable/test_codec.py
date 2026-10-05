"""The journal's JSON codec: exact round-trips, and a journal row cannot make
a resuming worker run code."""

from __future__ import annotations

import dataclasses
import datetime as dt
import decimal
import enum
import json
import uuid
from collections import namedtuple
from pathlib import PurePosixPath

import pytest
from pydantic import BaseModel

from operonx.core.ops._events import SELF_CTX, Failure, Interrupt
from operonx.durable import JournalError, MemoryJournal, RunHeader, Step
from operonx.durable.codec import CodecError, decode, digest, dumps, encode, loads

pytestmark = pytest.mark.unit


class Colour(enum.Enum):
    RED = "red"


@dataclasses.dataclass(frozen=True)
class Order:
    id: int
    lines: tuple
    note: "Note" = None


class Note(BaseModel):
    text: str
    tags: list = []


Point = namedtuple("Point", "x y")


class Counter_:  # a plain class: not something a journal rebuilds
    pass


VALUES = [
    None,
    True,
    0,
    -3,
    1.5,
    "tiếng Việt",
    [1, (2, 3)],
    (1, [2, (3,)]),
    {"a": (1, 2), "$t": "a key named like the tag"},
    {1: "int key", (1, 2): "tuple key"},
    {1, 2},
    frozenset({"x"}),
    b"\x00\xffbytes",
    dt.datetime(2026, 10, 5, 8, 30, tzinfo=dt.timezone.utc),
    dt.date(2026, 10, 5),
    dt.time(8, 30),
    dt.timedelta(days=1, seconds=3, microseconds=4),
    decimal.Decimal("1.10"),
    uuid.UUID(int=7),
    PurePosixPath("/srv/a.txt"),
    Colour.RED,
    Order(7, (1, 2), Note(text="hi", tags=[(1, 2)])),
    Point(1, (2, 3)),
    Failure(op="g.a", ctx=("main",), error="E: x", inputs={"t": (1,)}),
    Interrupt(op="g.a", ctx=("main",), reason="r"),
]


@pytest.mark.parametrize("value", VALUES, ids=lambda v: type(v).__name__)
def test_a_value_comes_back_exactly(value):
    back = loads(dumps(value))
    assert back == value and type(back) is type(value)


def test_nested_types_survive():
    back = loads(dumps(Order(7, (1, 2), Note(text="hi", tags=[(1, 2)]))))
    assert isinstance(back.lines, tuple) and isinstance(back.note, Note)
    assert back.note.tags == [(1, 2)] and isinstance(back.note.tags[0], tuple)


def test_the_self_sentinel_is_the_one_instance():
    assert loads(dumps(Interrupt(reason="r"))).ctx_to_cancel is SELF_CTX


def test_what_has_no_encoding_is_refused():
    for value in (lambda: 1, object(), Counter_(), open):
        with pytest.raises(CodecError, match="cannot be journalled"):
            encode(value)


@pytest.mark.parametrize(
    "forged",
    [
        {"$t": "dc", "cls": "os:system", "v": {}},  # a function, not a class
        {"$t": "dc", "cls": "subprocess:Popen", "v": {"args": "id"}},  # a class, not a dataclass
        {"$t": "model", "cls": "builtins:dict", "v": {}},
        {"$t": "enum", "cls": "os:system", "v": "id"},
        {"$t": "dc", "cls": "no.such.module:X", "v": {}},
        {"$t": "pickle", "v": "gASV"},
    ],
)
def test_a_forged_row_cannot_run_code(forged):
    with pytest.raises(CodecError):
        decode(forged)


def test_a_dataclass_is_rebuilt_without_running_its_init():
    calls = []

    @dataclasses.dataclass
    class Loud:
        x: int

        def __post_init__(self):
            calls.append(self.x)

    globals()["Loud"] = Loud  # importable by name, as a module-level class is
    Loud.__qualname__ = "Loud"
    blob = dumps(Loud(1))
    calls.clear()
    back = loads(blob)
    assert type(back) is Loud and back.x == 1
    assert calls == []  # its __post_init__ did not run on the way back


def test_digest_is_stable_and_order_blind():
    assert digest({"a": 1, "b": (2,)}) == digest({"b": (2,), "a": 1})
    assert digest((1, 2)) != digest([1, 2])  # a tuple is not a list


def test_the_journal_is_json_on_disk(tmp_path):
    from operonx.durable import SqliteJournal

    journal = SqliteJournal(tmp_path / "j.db")
    journal.open_run(RunHeader("r", "fp", inputs={"x": (1, 2)}))
    journal.append("r", [Step(".a", ("main",), 0, [(".a", "y", ("main",), Colour.RED)])])
    import sqlite3

    raw = sqlite3.connect(tmp_path / "j.db").execute("SELECT step FROM steps").fetchone()[0]
    assert json.loads(raw)["$t"] == "dc"  # readable without operonx
    header, steps = journal.read("r")
    assert header.inputs == {"x": (1, 2)} and steps[0].writes[0][3] is Colour.RED


def test_an_unjournalable_write_names_its_op_and_var():
    journal = MemoryJournal()
    journal.open_run(RunHeader("r", "fp"))
    with pytest.raises(JournalError, match=r"\.a\.y wrote a function"):
        journal.append("r", [Step(".a", ("main",), 0, [(".a", "y", ("main",), lambda: 1)])])
