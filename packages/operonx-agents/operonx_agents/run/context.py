"""``RunContext`` — what a tool receives besides its arguments.

A tool that declares a first parameter annotated ``RunContext`` (or
``RunContext[Deps]``) gets the context there; the model never sees that
parameter. ``deps`` is whatever the caller passed in: a CRM client, the
tenant, a limit.

A tool that runs another agent passes its context on, and the child's
spending counts toward this run's limits::

    @tool
    async def ask_billing(ctx: RunContext, question: str) -> str:
        res = await Runner.run(billing, question, parent=ctx)
        return res.output
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Generic, Optional, TypeVar

from operonx_agents.model.usage import Usage

if TYPE_CHECKING:
    from operonx_agents.run.limits import Meter

Deps = TypeVar("Deps")

__all__ = ["RunContext"]


@dataclass
class RunContext(Generic[Deps]):
    """The context of one run, as a tool sees it.

    Attributes:
        deps: The caller's dependencies, passed through untouched.
        tool_call_id: The id of the call being executed, set by dispatch
            for the duration of that call.
        metadata: Free-form values the caller wants every tool to see.
        run_id: The run's id; also its :class:`~operonx_agents.RunState`'s.
        session_id: The session the run reads and writes, if any.
        agent: The name of the agent running.
        turn: The turn the call was made in (1-based).
        store: Where the run is saved, if anywhere: a tool that runs
            another agent saves that run beside it, so an approval inside
            it can wait too.
        approvals: The answers ``Runner.resume`` was given, by
            interruption id; a sub-agent's resume takes its own.
    """

    deps: Deps = None  # type: ignore[assignment]
    tool_call_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    agent: Optional[str] = None
    turn: int = 0
    meter: Optional["Meter"] = field(default=None, repr=False, compare=False)
    store: Any = field(default=None, repr=False, compare=False)
    approvals: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def usage(self) -> Usage:
        """What the run has spent so far, its children's spending included."""
        return self.meter.total if self.meter is not None else Usage()
