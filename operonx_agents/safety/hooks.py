"""Hooks — the one extension point around the loop.

::

    class Guard(Hooks):
        async def before_tool(self, ctx, call):
            if call.name == "refund" and call.args["amount"] > 500:
                return Ask("refunds over 500 need a manager")
            if call.name == "refund":
                return call.replace(args={**call.args, "currency": "VND"})

    Agent(..., hooks=[Guard(), RedactToolOutput()])

Each method may return ``None`` (no opinion), a replacement, or raise
:class:`~operonx_agents.Tripwire`, which ends the run ``status="blocked"``
writing nothing for the turn it cut:

=================================  ==========================================
``before_model(ctx, request)``     a :class:`ModelRequest` to send instead
``after_model(ctx, response)``     a ``ModelResponse`` to act on instead
``before_tool(ctx, call)``         a :class:`ToolCall` with other arguments,
                                   :class:`Ask` or :class:`Deny`
``after_tool(ctx, call, content)`` the text the model reads instead
``on_output(ctx, output)``         the run's output instead
=================================  ==========================================

Hooks run in order, each seeing the previous one's replacement. For
``before_tool`` the policy is one more voice: the verdicts merge as
deny > ask > allow, so a hook can tighten what the policy allows and never
loosen it — an ``allow`` that skipped the policy is the documented
``canUseTool`` footgun (track3 §3.2). A ``Deny`` is a refusal the model
reads; it never reaches a human (deny is not ask). An ``Ask`` parks the
call as an :class:`~operonx_agents.Interruption`.

An agent with no hooks pays nothing: :class:`HookSet` keeps, per method,
only the hooks that override it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Union

from operonx_agents.run.interruption import Deny

if TYPE_CHECKING:
    from operonx_agents.model.model import ModelResponse
    from operonx_agents.run.context import RunContext

__all__ = ["Ask", "Deny", "HookSet", "Hooks", "ModelRequest", "ToolCall"]


@dataclass(frozen=True)
class ToolCall:
    """A call as ``before_tool`` / ``after_tool`` see it: ``args`` are
    validated (the tool's own types)."""

    id: str
    name: str
    args: Dict[str, Any]

    def replace(self, *, args: Dict[str, Any]) -> "ToolCall":
        """The same call with other arguments (they are validated again)."""
        return ToolCall(self.id, self.name, args)


@dataclass(frozen=True)
class ModelRequest:
    """What the next model call sends: the conversation (no system prompt)
    and the tool definitions offered."""

    messages: List[Dict[str, Any]]
    tools: Optional[List[Dict[str, Any]]] = None


@dataclass(frozen=True)
class Ask:
    """``before_tool``'s verdict: a human must approve this call first."""

    reason: str = field(default="")


#: What ``before_tool`` may return.
ToolVerdict = Union[None, ToolCall, Ask, Deny]


class Hooks:
    """Subclass and override the methods you need; the defaults have no
    opinion."""

    async def before_model(
        self, ctx: "RunContext", request: ModelRequest
    ) -> Optional[ModelRequest]:
        return None

    async def after_model(
        self, ctx: "RunContext", response: "ModelResponse"
    ) -> Optional["ModelResponse"]:
        return None

    async def before_tool(self, ctx: "RunContext", call: ToolCall) -> ToolVerdict:
        return None

    async def after_tool(self, ctx: "RunContext", call: ToolCall, content: str) -> Optional[str]:
        return None

    async def on_output(self, ctx: "RunContext", output: Any) -> Any:
        return None


_METHODS = ("before_model", "after_model", "before_tool", "after_tool", "on_output")


class HookSet:
    """An agent's hooks, sorted by the method each overrides."""

    __slots__ = ("hooks", *_METHODS)

    def __init__(self, hooks: Sequence[Hooks] = ()) -> None:
        self.hooks = tuple(hooks)
        for hook in self.hooks:
            if not isinstance(hook, Hooks):
                raise TypeError(
                    f"hooks= takes Hooks instances, got {type(hook).__name__}. Subclass "
                    "operonx_agents.Hooks and override the methods you need."
                )
        for method in _METHODS:
            base = getattr(Hooks, method)
            mine = [h for h in self.hooks if getattr(type(h), method) is not base]
            setattr(self, method, tuple(mine))

    def __bool__(self) -> bool:
        return bool(self.hooks)

    async def model_request(self, ctx: "RunContext", request: ModelRequest) -> ModelRequest:
        for hook in self.before_model:
            replaced = await hook.before_model(ctx, request)
            if replaced is not None:
                request = _expect(replaced, ModelRequest, hook, "before_model")
        return request

    async def model_response(self, ctx: "RunContext", response: "ModelResponse") -> "ModelResponse":
        from operonx_agents.model.model import ModelResponse

        for hook in self.after_model:
            replaced = await hook.after_model(ctx, response)
            if replaced is not None:
                response = _expect(replaced, ModelResponse, hook, "after_model")
        return response

    async def tool_content(self, ctx: "RunContext", call: ToolCall, content: str) -> str:
        for hook in self.after_tool:
            replaced = await hook.after_tool(ctx, call, content)
            if replaced is not None:
                content = _expect(replaced, str, hook, "after_tool")
        return content

    async def output(self, ctx: "RunContext", output: Any) -> Any:
        for hook in self.on_output:
            replaced = await hook.on_output(ctx, output)
            if replaced is not None:
                output = replaced
        return output


def _expect(value: Any, kind: type, hook: Hooks, method: str) -> Any:
    if not isinstance(value, kind):
        raise TypeError(
            f"{type(hook).__name__}.{method} returned {type(value).__name__}; it returns "
            f"None (no opinion) or a {kind.__name__}."
        )
    return value
