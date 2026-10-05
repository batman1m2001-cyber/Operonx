"""Durable execution: a run journal a crashed run resumes from.

``Operon(g, journal=SqliteJournal("runs.db"))`` records each execution's
yields and end with the cell writes they made; ``await engine.resume(run_id)``
— on any worker that opens the same journal — restores the cells, replays
what ended and runs the rest. Off by default: without a journal the
scheduler pays one ``is None`` test per execution. See
docs/RUNTIME_R3_PLAN.md.
"""

from .fingerprint import graph_fingerprint
from .journal import END, Journal, JournalError, MemoryJournal, RunHeader, SqliteJournal, Step
from .recorder import DURABILITY, ON_RESUME, NonDeterministicResume, RunRecorder

__all__ = [
    "DURABILITY",
    "END",
    "ON_RESUME",
    "Journal",
    "JournalError",
    "MemoryJournal",
    "NonDeterministicResume",
    "RunHeader",
    "RunRecorder",
    "SqliteJournal",
    "Step",
    "graph_fingerprint",
]
