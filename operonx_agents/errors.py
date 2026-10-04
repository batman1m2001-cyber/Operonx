"""The exceptions ``operonx_agents`` raises, and the one a tool raises.

Every message says what went wrong and what to do about it, the operonx
convention. A failure the *model* can fix (bad arguments, an answer that
does not validate) never reaches the caller as an exception: it becomes a
message the model reads. These are for the caller.
"""

from __future__ import annotations

from typing import Any, List, Optional

__all__ = [
    "AgentsError",
    "Interrupted",
    "ModelRetry",
    "ModelError",
    "ModelTimeout",
    "ModelRefused",
    "OutputInvalid",
    "ToolDefinitionError",
    "Tripwire",
]


class AgentsError(Exception):
    """Base class of every error this package raises."""


class ModelRetry(Exception):
    """Raised by a tool to hand the model a correction instead of a result.

    The text becomes the tool's message, so the model can call again with
    better arguments::

        @tool
        async def refund(ctx: RunContext[Deps], order_id: str, amount: float) -> str:
            if amount > ctx.deps.limit:
                raise ModelRetry(f"amount exceeds the {ctx.deps.limit} limit; ask a human")
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Tripwire(AgentsError):
    """Raised by a hook to stop the run: it ends ``status="blocked"`` with
    ``reason`` as its error, and the turn it cut writes nothing::

        class NoRefundsOver(Hooks):
            async def before_tool(self, ctx, call):
                if call.name == "refund" and call.args["amount"] > 500:
                    raise Tripwire("refund over 500 requested")
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Interrupted(AgentsError):
    """A tool call waits for a human: the run stops ``status="interrupted"``
    and is continued with ``Runner.resume(..., approvals=...)``.

    The runner raises it for a call that needs approval, and a tool that
    runs another agent raises it with that agent's interruptions, so an
    approval deep in a sub-agent surfaces on the run a human can answer.
    ``interruptions`` holds :class:`~operonx_agents.Interruption`\\ s.
    """

    def __init__(self, interruptions: List[Any]) -> None:
        super().__init__(
            "waiting for a human on " + ", ".join(f"{i.tool!r} ({i.id})" for i in interruptions)
        )
        self.interruptions = list(interruptions)


class ToolDefinitionError(AgentsError, TypeError):
    """A function cannot be a tool as written (raised by ``@tool``)."""


class ModelError(AgentsError):
    """Every resource of a :class:`~operonx_agents.Model` failed.

    ``attempts`` holds one ``(resource, error)`` per resource tried, in
    order, so the log says which gateway did what.
    """

    def __init__(self, message: str, attempts: Optional[List[tuple]] = None) -> None:
        super().__init__(message)
        self.attempts = list(attempts or [])


class ModelRefused(ModelError):
    """Every resource refused (``finish_reason`` ``content_filter``/``safety``,
    or a ``refusal``). A different prompt may help; retrying will not."""


class ModelTimeout(AgentsError, TimeoutError):
    """The model's ``deadline`` passed before an answer, over the whole
    chain: the primary, its fallbacks and every re-ask."""

    def __init__(self, deadline: float, resource: str) -> None:
        super().__init__(
            f"model {resource!r} gave no answer within its {deadline}s deadline (the deadline "
            "covers the fallbacks and re-asks too). Raise Model(deadline=...), or map the "
            "timeout to a value: llm_step(on_timeout=...)."
        )
        self.deadline = deadline
        self.resource = resource


class OutputInvalid(AgentsError, ValueError):
    """The model's answer failed validation on the first ask and on every
    re-ask (``output_retries``). ``answer`` is the last raw answer and
    ``error`` the last validation error."""

    def __init__(self, message: str, answer: Any = None, error: str = "") -> None:
        super().__init__(message)
        self.answer = answer
        self.error = error
