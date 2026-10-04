"""Approvals as interruptions: data a run stops on, not a future it waits on.

A call that needs a human does not hold the process. The run saves its
:class:`RunState` with the turn parked and returns
``status="interrupted"`` with one :class:`Interruption` per waiting call;
whoever can answer (a person, another service, another process a day
later) continues it::

    res = await Runner.run(agent, "refund A1B2C3D4", store=store)
    if res.status == "interrupted":
        ...                                  # show res.interruptions to a human
        res = await Runner.resume(agent, res.run_id, store=store,
                                  approvals={i.id: Approve() for i in res.interruptions})

An interruption's ``id`` is operonx's :func:`~operonx.core.runtime.invocation_key`
of (run, ``<agent>.<tool>``, ``(turn[n], <call id>)``) — the scheme an
``InterruptOp`` uses for its events (R2): the same question in the same
place of the same run always has the same id, so a resume in another
process finds it, and nothing else has it.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, Optional, Tuple, Union

from operonx.core.runtime import invocation_key

__all__ = ["Approve", "Deny", "Decision", "Interruption", "child_run_id", "interruption_id"]


def interruption_id(run_id: str, agent: str, tool: str, turn: int, call_id: str) -> str:
    """The id of the approval a call in ``turn`` of run ``run_id`` waits on:
    ``invocation_key(run_id, "<agent>.<tool>", ("turn[<turn>]", call_id))``."""
    return invocation_key(run_id, f"{agent}.{tool}", (f"turn[{turn}]", call_id))


def child_run_id(run_id: str, agent: str, tool: str, turn: int, call_id: str) -> str:
    """The run id of the agent a call runs as a tool (``Agent.as_tool``):
    the same key with the call's ctx extended by ``"agent"``, so a parent
    resumed in another process finds its child's run, and the id is not
    the call's own interruption id."""
    return invocation_key(run_id, f"{agent}.{tool}", (f"turn[{turn}]", call_id, "agent"))


@dataclass(frozen=True)
class Interruption:
    """One tool call waiting for a human.

    Attributes:
        id: What :meth:`Runner.resume`'s ``approvals`` is keyed by.
        tool: The tool the call wants to run.
        args: Its validated arguments, redacted (the agent's ``redact``):
            what a human is shown, never what the tool runs with.
        reason: Why it asks: the tool's ``approval``, the policy's
            ``ask``, or a hook's :class:`~operonx_agents.Ask`.
        call_id: The model's id for the call.
        path: The agents from the run that returned it down to the one
            whose call waits: ``("support",)``, or ``("support",
            "billing")`` for a sub-agent's call.
        expires_at: Unix time after which an answer no longer counts and
            the call is refused; ``None`` never expires.
    """

    id: str
    tool: str
    args: Dict[str, Any]
    reason: str
    call_id: str
    path: Tuple[str, ...]
    expires_at: Optional[float] = None

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and time.time() >= self.expires_at

    def under(self, agent: str) -> "Interruption":
        """This interruption as the agent that delegated to its run sees it."""
        return replace(self, path=(agent, *self.path))

    def to_json(self) -> Dict[str, Any]:
        out = asdict(self)
        out["path"] = list(self.path)
        return out

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Interruption":
        return cls(**{**data, "path": tuple(data.get("path") or ())})


@dataclass(frozen=True)
class Approve:
    """Run the call as the model asked."""


@dataclass(frozen=True)
class Deny:
    """Refuse the call. ``reason`` is shown to the model with the refusal."""

    reason: str = field(default="")


#: A human's answer to one :class:`Interruption`.
Decision = Union[Approve, Deny]
