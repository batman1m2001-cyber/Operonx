"""Run a model turn's tool calls: one tool message per call, always.

For each call, in this order: policy, unknown tool, arguments, approval,
run. Every outcome — success, :class:`~operonx_agents.ModelRetry`, invalid
arguments, a timeout, an exception, a refusal, an unknown tool — is one
``role: "tool"`` message answering that call's id. A provider rejects a
conversation where a call has no answer, so nothing here raises for a
call's sake; the model reads what went wrong and tries again.

Concurrency: the calls whose tool is not ``sequential`` (by default the
``readonly`` ones) run together first; the rest run one at a time, in the
order the model emitted them. The messages come back in emitted order.

Each call is recorded as a child execution of the op running dispatch
(``operonx.child``, ``op_type="tool"``) with its arguments, its message
and the GenAI attributes; outside a traced run that costs nothing.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import inspect
import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

import pydantic
from operonx import child
from operonx.providers.llms.base import normalize_tool_call

from operonx_agents.errors import ModelRetry
from operonx_agents.run.context import RunContext
from operonx_agents.tools.policy import DEFAULT_POLICY, ToolPolicy
from operonx_agents.tools.tool import Tool, ToolSpec
from operonx_agents.tools.toolset import Toolset

__all__ = ["Approver", "dispatch", "tool_message"]

#: ``await approve(call, spec) -> bool``: a human's answer to one call that
#: needs approval. ``call`` is ``{"id", "name", "args"}`` with validated args.
Approver = Callable[[Dict[str, Any], ToolSpec], Awaitable[bool]]

#: ``on_start(call)``: a call passed every check and its tool is about to
#: run; ``call`` is ``{"id", "name", "args"}`` with validated args.
OnStart = Callable[[Dict[str, Any]], None]

#: ``await on_message(message)``: a call's one tool message is ready.
OnMessage = Callable[[dict], Awaitable[None]]

# The wording the model reads. Kept from operonx.agents' dispatch, where
# each was tuned against a live model: a named error beats a silent absence.
UNKNOWN_TOOL = (
    "Error: no tool named {name!r}. Available tools: {available}. "
    "Call one of those, or answer without a tool."
)
BAD_JSON = "Error: could not parse arguments for {name!r}: {error}. Expected a JSON object."
BAD_ARGS = "Error: invalid arguments for {name!r}: {error}. Fix them and call it again."
EXEC_ERROR = "Error: tool {name!r} failed: {error}"
TIMEOUT = "Error: tool {name!r} timed out after {timeout}s."
DO_NOT_RETRY = "Do not retry it; ask how to proceed."
DENIED = "Blocked: a human declined this {name!r} call. " + DO_NOT_RETRY
NO_APPROVER = (
    "Blocked: {name!r} needs a human's approval and this run has no way to ask one. " + DO_NOT_RETRY
)


_UNSAFE = re.compile(r"[.\[\]#]")


def tool_message(call_id: str, name: str, content: str, *, is_error: bool = False) -> dict:
    """The one shape every dispatch outcome returns (``operonx.agents``'
    shape: ``name`` and ``status`` are bookkeeping the backends strip)."""
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": content,
        "status": "error" if is_error else "success",
    }


async def dispatch(
    calls: Sequence[Any],
    toolset: Toolset,
    *,
    ctx: Optional[RunContext] = None,
    policy: Optional[ToolPolicy] = None,
    approve: Optional[Approver] = None,
    on_start: Optional[OnStart] = None,
    on_message: Optional[OnMessage] = None,
) -> List[dict]:
    """Run ``calls`` against ``toolset``; one tool message per call, in order.

    Args:
        calls: The model's tool calls, in any shape
            ``normalize_tool_call`` reads (``LLMOp`` and
            :class:`~operonx_agents.Model` give ``{"id", "name", "args"}``).
        toolset: The tools this agent owns — the only ones that can run.
        ctx: Handed to tools that take a ``RunContext``; each call gets a
            copy with its own ``tool_call_id``.
        policy: allow / ask / deny per tool. Default: destructive tools
            ask, everything else runs.
        approve: How a call that needs a human is asked. Without one,
            such a call is refused (fail closed): a gate that opens when
            nobody can answer is decoration.
        on_start: Told when a call's tool is about to run (the runner's
            ``ToolCallStarted``).
        on_message: Awaited with each call's message as soon as it is
            ready, in completion order (the runner journals it).
    """
    policy = policy or DEFAULT_POLICY
    base_ctx = ctx if ctx is not None else RunContext()
    normal = [normalize_tool_call(c) for c in calls]
    out: List[Optional[dict]] = [None] * len(normal)

    async def run(index: int) -> None:
        out[index] = await _one(normal[index], toolset, base_ctx, policy, approve, on_start)
        if on_message is not None:
            await on_message(out[index])

    together = [i for i, c in enumerate(normal) if not _sequential(toolset.get(c["name"]))]
    if together:
        # The first runs in this task, the others as tasks that start as
        # soon as it waits on anything: as concurrent as a gather, one task
        # fewer per batch (and none for a lone call).
        others = [asyncio.ensure_future(run(i)) for i in together[1:]]
        try:
            await run(together[0])
            for task in others:
                await task
        finally:
            for task in others:
                task.cancel()
    for i, call in enumerate(normal):
        if out[i] is None:
            await run(i)
    return out  # type: ignore[return-value]


@functools.lru_cache(maxsize=1024)
def _label(name: str) -> str:
    """The trace name of a call. The model chose ``name``, and a trace
    segment cannot hold ``.``, ``[``, ``]`` or ``#``."""
    return _UNSAFE.sub("_", name) or "tool"


def _sequential(t: Optional[Tool]) -> bool:
    # An unknown tool only produces a message; it may go with the batch.
    return t is not None and t.spec.sequential


async def _one(
    call: Dict[str, Any],
    toolset: Toolset,
    ctx: RunContext,
    policy: ToolPolicy,
    approve: Optional[Approver],
    on_start: Optional[OnStart],
) -> dict:
    call_id, name, raw = call["id"], call["name"], call["args"]
    found = toolset.get(name)
    label = _label(name)
    async with child(label, inputs={"args": raw}, op_type="tool") as rec:
        rec.attrs.update(
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
                "gen_ai.tool.call.id": call_id,
            }
        )
        message = await _answer(call_id, name, raw, found, toolset, ctx, policy, approve, on_start)
        rec.outputs = {"tool_message": message}
        return message


async def _answer(call_id, name, raw, found, toolset, ctx, policy, approve, on_start) -> dict:
    def say(content: str, *, error: bool = False) -> dict:
        limit = found.spec.max_result_chars if found is not None else 0
        return tool_message(call_id, name, _truncate(content, limit), is_error=error)

    meta = (
        {"readonly": found.spec.readonly, "destructive": found.spec.destructive}
        if found is not None
        else {}
    )
    # Arguments that are not even JSON are reported as such: a policy
    # verdict on a call that could never run tells the model nothing.
    if not isinstance(raw, dict):
        return say(BAD_JSON.format(name=name, error=f"got {raw!r}"), error=True)
    # Policy before the unknown-tool check: `rules={"shell": "deny"}`
    # holds even when no such tool is loaded, so the model is not told the
    # capability is merely absent and invited to look for another route.
    decision = policy.decide(name, meta)
    if decision == "deny":
        return say(policy.refusal(name), error=True)
    if found is None:
        return say(
            UNKNOWN_TOOL.format(name=name, available=", ".join(toolset.names) or "(none)"),
            error=True,
        )
    try:
        args = found.validate(raw)
    except pydantic.ValidationError as exc:
        return say(BAD_ARGS.format(name=name, error=_field_errors(exc)), error=True)

    call_ctx = ctx
    if found.takes_context or not isinstance(found.spec.approval, str):
        call_ctx = copy.copy(ctx)  # per call; deps and the meter are shared
        call_ctx.tool_call_id = call_id
    if decision == "ask" or _needs_approval(found.spec, call_ctx, args):
        if approve is None:
            return say(NO_APPROVER.format(name=name), error=True)
        if not await approve({"id": call_id, "name": name, "args": args}, found.spec):
            return say(DENIED.format(name=name), error=True)

    if on_start is not None:
        on_start({"id": call_id, "name": name, "args": args})
    try:
        result = found.function(call_ctx, **args) if found.takes_context else found.function(**args)
        if inspect.isawaitable(result):
            timeout = found.spec.timeout
            result = await (asyncio.wait_for(result, timeout) if timeout else result)
    except asyncio.TimeoutError:
        return say(TIMEOUT.format(name=name, timeout=found.spec.timeout), error=True)
    except ModelRetry as retry:
        return say(retry.message, error=True)
    except Exception as exc:  # noqa: BLE001 - every failure must reach the model
        return say(EXEC_ERROR.format(name=name, error=f"{type(exc).__name__}: {exc}"), error=True)
    return say(_render(result))


def _needs_approval(spec: ToolSpec, ctx: RunContext, args: Dict[str, Any]) -> bool:
    if spec.approval == "always":
        return True
    if spec.approval == "never":
        return False
    # A rule that errors fails closed: it asks.
    try:
        return bool(spec.approval(ctx, args))
    except Exception:  # noqa: BLE001
        return True


def _field_errors(exc: pydantic.ValidationError) -> str:
    """``order_id: Field required; days: Input should be ...`` — each bad
    field by name, which is what lets the model fix exactly that one."""
    parts = []
    for err in exc.errors(include_url=False):
        where = ".".join(str(p) for p in err["loc"]) or "(arguments)"
        parts.append(f"{where}: {err['msg']}")
    return "; ".join(parts)


def _render(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, pydantic.BaseModel):
        return result.model_dump_json()
    try:
        return json.dumps(result, ensure_ascii=False, default=_jsonable)
    except (TypeError, ValueError):
        return str(result)


def _jsonable(value: Any) -> Any:
    if isinstance(value, pydantic.BaseModel):
        return value.model_dump(mode="json")
    return str(value)


def _truncate(text: str, limit: int) -> str:
    if limit and len(text) > limit:
        return f"{text[:limit]}\n\n[truncated: {len(text) - limit} more characters]"
    return text
