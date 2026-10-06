"""The events ``Runner.stream`` yields — a closed set of frozen dataclasses.

In order, for a run::

    RunStarted
      TurnStarted
        Compacted?                       when the context was compacted first
        (TextDelta | ReasoningDelta)*    as the model streams
        (ToolCallStarted? ToolCallFinished)*   one Finished per call; a call
                                         refused before it ran has no Started
        ApprovalRequired*                a call parked for a human, instead
                                         of its Finished
      TurnFinished                       only for a committed turn
    RunFinished                          always last, carrying the RunResult

A turn that does not commit (the model failed, a cap cut it, the wall
clock ran out, a call waits for a human) has a ``TurnStarted`` and no
``TurnFinished``; the run's ``RunFinished`` says why. A resumed run
starts again with ``RunStarted(resumed=True)`` and the parked turn.
``to_json()`` gives one plain dict per event, a ``type`` key first, for an
SSE or WebSocket frame.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Dict, Tuple, Union

from operonx_agents.model.usage import Usage
from operonx_agents.run.result import RunResult

__all__ = [
    "ApprovalRequired",
    "Compacted",
    "Event",
    "ReasoningDelta",
    "RunFinished",
    "RunStarted",
    "TextDelta",
    "ToolCallFinished",
    "ToolCallStarted",
    "TurnFinished",
    "TurnStarted",
]


class _Event:
    __slots__ = ()

    def to_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"type": type(self).__name__}
        for f in fields(self):  # type: ignore[arg-type]
            value = getattr(self, f.name)
            if isinstance(value, Usage):
                value = value.to_dict()
            elif isinstance(value, RunResult):
                value = value.to_dict()
            elif isinstance(value, tuple):
                value = list(value)
            out[f.name] = value
        return out


@dataclass(frozen=True)
class RunStarted(_Event):
    run_id: str
    agent: str
    resumed: bool = False


@dataclass(frozen=True)
class TurnStarted(_Event):
    turn: int


@dataclass(frozen=True)
class TextDelta(_Event):
    """A piece of the answer's text, as the model streams it."""

    text: str


@dataclass(frozen=True)
class ReasoningDelta(_Event):
    """A piece of the model's thinking; never part of the answer."""

    text: str


@dataclass(frozen=True)
class ToolCallStarted(_Event):
    """A call passed validation and policy and is about to run."""

    call_id: str
    tool: str
    args: Dict[str, Any]


@dataclass(frozen=True)
class ToolCallFinished(_Event):
    """A call's one tool message is ready. ``ok`` is false for every
    error the model reads (bad arguments, unknown tool, a refusal, an
    exception, a timeout); ``ms`` is 0 for a call that never ran."""

    call_id: str
    tool: str
    ok: bool
    result_preview: str
    ms: float


@dataclass(frozen=True)
class ApprovalRequired(_Event):
    """A call waits for a human; the run will end ``interrupted``. ``args``
    are redacted; ``id`` is what ``Runner.resume``'s ``approvals`` takes."""

    id: str
    call_id: str
    tool: str
    args: Dict[str, Any]
    reason: str
    path: Tuple[str, ...]


@dataclass(frozen=True)
class Compacted(_Event):
    """Older exchanges were replaced by a summary before the turn's call."""

    dropped: int
    summary_tokens: int


@dataclass(frozen=True)
class TurnFinished(_Event):
    """A turn was committed. ``usage`` is what its model call spent."""

    turn: int
    usage: Usage


@dataclass(frozen=True)
class RunFinished(_Event):
    result: RunResult


Event = Union[
    ApprovalRequired,
    RunStarted,
    TurnStarted,
    TextDelta,
    ReasoningDelta,
    ToolCallStarted,
    ToolCallFinished,
    Compacted,
    TurnFinished,
    RunFinished,
]
