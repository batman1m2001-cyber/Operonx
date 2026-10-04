"""``RunState`` — everything a run needs to continue, as versioned JSON.

The runner writes it to a :class:`~operonx_agents.StateStore` at turn
boundaries, so crash recovery and continuing a failed run are the same
thing: load it, continue (``Runner.resume``).

One more write happens inside a turn, the crash-recovery journal: before
a turn's tools run, the turn is saved as :attr:`RunState.pending` with
every call in :attr:`PendingTurn.inflight`; each call that finishes moves
from ``inflight`` to ``results``. A run resumed after a crash re-runs an
in-flight call whose tool is ``idempotent`` and answers any other one
"outcome unknown" — the side-effect journal of Temporal and DBOS, at turn
granularity, with no workflow engine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

from pydantic import TypeAdapter

from operonx_agents.model.usage import Usage

__all__ = ["PendingTurn", "RunState", "Status", "STATE_VERSION"]

STATE_VERSION = 1

#: ``running`` until the run ends; a crashed run stays ``running``.
Status = Literal["running", "completed", "limit", "failed"]


@dataclass
class PendingTurn:
    """A turn whose tools were running when it was saved.

    Attributes:
        items: The turn's session items so far, the assistant message that
            made the calls last.
        messages: The conversation as the model saw it, plus that
            assistant message.
        calls: Those calls, ``{"id", "name", "args"}``, in emitted order.
        inflight: Ids of the calls with no result yet.
        results: The tool messages of the calls that finished, by id.
    """

    items: List[Dict[str, Any]]
    messages: List[Dict[str, Any]]
    calls: List[Dict[str, Any]]
    inflight: List[str]
    results: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass(kw_only=True)
class RunState:
    """One run, resumable. A plain dataclass the loop updates in place
    (it is written on every turn); serialised through pydantic.

    Attributes:
        version: The format's version; a store refuses one it cannot read.
        run_id: The run's id (its key in the store).
        agent: The agent's name; a resume checks it is given the same.
        status: Where the run is.
        turn: Turns committed.
        input: The run's input messages until the first turn commits them.
        messages: The conversation the model sees, without the system
            prompt: the session's view when the run started, plus every
            committed turn.
        new_items: What the committed turns add to the session, in order
            (compaction summaries included).
        saved: How many of ``new_items`` the session has.
        session_id: The session it writes, if any.
        session_base: Items the session held when the run started. A
            resume compares it with the session's length, so a crash
            between the session write and the state write does not write
            a turn twice.
        usage: What the run has spent, children included.
        tool_calls: Tool calls run.
        reasks: Answers re-asked because they did not validate.
        last_input: The last request's prompt tokens: the next prompt is
            at least as long, so a token cap is checked against both.
        final_turn: The cap (``turns`` or ``tool_calls``) that makes the
            next turn the last one.
        compact_next: The last prompt crossed the compaction threshold.
        pending: The turn whose tools were running, if any.
        output / limit_hit / error / finish_reason: How it ended.
    """

    version: int = STATE_VERSION
    run_id: str
    agent: str
    status: Status = "running"
    turn: int = 0
    input: List[Dict[str, Any]] = field(default_factory=list)
    messages: List[Dict[str, Any]] = field(default_factory=list)
    new_items: List[Dict[str, Any]] = field(default_factory=list)
    saved: int = 0
    session_id: Optional[str] = None
    session_base: int = 0
    usage: Dict[str, Any] = field(default_factory=lambda: Usage().to_dict())
    tool_calls: int = 0
    reasks: int = 0
    last_input: int = 0
    final_turn: Optional[str] = None
    compact_next: bool = False
    pending: Optional[PendingTurn] = None
    output: Any = None
    limit_hit: Optional[str] = None
    error: Optional[str] = None
    finish_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def dumps(self) -> str:
        return _ADAPTER.dump_json(self).decode()

    @classmethod
    def loads(cls, text: str) -> "RunState":
        state = _ADAPTER.validate_json(text)
        if state.version != STATE_VERSION:
            raise ValueError(
                f"run {state.run_id!r} was saved in RunState format {state.version}; this "
                f"operonx-agents reads format {STATE_VERSION}. Resume it with the version "
                "that saved it."
            )
        return state


_ADAPTER = TypeAdapter(RunState)
