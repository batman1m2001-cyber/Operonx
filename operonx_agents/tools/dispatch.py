"""Run a model turn's tool calls: one tool message per call, always.

For each call, in this order: policy, unknown tool, arguments, the
``before_tool`` hooks, approval, run, the ``after_tool`` hooks,
truncation. Every outcome — success, :class:`~operonx_agents.ModelRetry`,
invalid arguments, a timeout, an exception, a refusal, an unknown tool —
is one ``role: "tool"`` message answering that call's id. A provider
rejects a conversation where a call has no answer, so nothing here raises
for a call's sake; the model reads what went wrong and tries again.

The one exception is a call that waits for a human
(:class:`~operonx_agents.Interrupted`): :func:`run_calls`, the runner's
entry, hands it back as :class:`Paused` instead of a message, and the run
parks the turn. :func:`dispatch` has no run to park, so it refuses such a
call (fail closed).

Concurrency: the calls whose tool is not ``sequential`` (by default the
``readonly`` ones) run together first; the rest run one at a time, in the
order the model emitted them. After a call pauses, no further sequential
call starts: the order the model asked for holds across the wait. The
messages come back in emitted order.

Each call is recorded as a child execution of the op running dispatch
(``operonx.child``, ``op_type="tool"``) with its arguments, its message
and the GenAI attributes — scrubbed by ``redact`` when one is given;
outside a traced run that costs nothing.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Union

import pydantic
from operonx import child
from operonx.providers.llms.base import normalize_tool_call

from operonx_agents.errors import Interrupted, ModelRetry
from operonx_agents.run.context import RunContext
from operonx_agents.run.interruption import Deny
from operonx_agents.safety.hooks import Ask, HookSet, ToolCall
from operonx_agents.tools.policy import DEFAULT_POLICY, ToolPolicy
from operonx_agents.tools.tool import Tool, ToolSpec
from operonx_agents.tools.toolset import Toolset

__all__ = ["Approver", "Gate", "Paused", "dispatch", "run_calls", "tool_message"]

#: ``await approve(call, spec) -> bool``: a human's answer to one call that
#: needs approval. ``call`` is ``{"id", "name", "args"}`` with validated args.
Approver = Callable[[Dict[str, Any], ToolSpec], Awaitable[bool]]

#: ``await gate(call, spec, reason) -> None | str``: how a call that needs
#: approval is decided — ``None`` runs it, a string is the refusal the model
#: reads, and :class:`~operonx_agents.Interrupted` parks it.
Gate = Callable[[ToolCall, ToolSpec, str], Awaitable[Optional[str]]]

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
#: A hook's ``Deny``: a refusal with the hook's reason, never a question.
HOOK_DENIED = "Blocked: {reason} This is not a transient failure; do not retry {name!r}."

_UNSAFE = re.compile(r"[.\[\]#]")


@dataclass(frozen=True)
class Paused:
    """A call that waits for a human: ``call`` is ``{"id", "name", "args"}``
    as the model sent it; ``interruptions`` what it waits on."""

    call: Dict[str, Any]
    interruptions: List[Any]


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
    hooks: Optional[HookSet] = None,
    redact: Any = None,
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
        hooks: ``before_tool`` / ``after_tool`` hooks (a :class:`HookSet`).
        redact: A :class:`~operonx_agents.Redactor` for what the trace
            records of each call; the tool and the model are untouched.
        on_start: Told when a call's tool is about to run (the runner's
            ``ToolCallStarted``).
        on_message: Awaited with each call's message as soon as it is
            ready, in completion order (the runner journals it).
    """
    outcomes = await run_calls(
        calls,
        toolset,
        ctx=ctx,
        policy=policy,
        gate=_approver_gate(approve),
        hooks=hooks,
        redact=redact,
        on_start=on_start,
        on_message=on_message,
    )
    # A call waiting on a human (a sub-agent's) has no run here to wait in.
    return [
        o
        if isinstance(o, dict)
        else tool_message(
            o.call["id"], o.call["name"], NO_APPROVER.format(name=o.call["name"]), is_error=True
        )
        for o in outcomes
    ]


async def run_calls(
    calls: Sequence[Any],
    toolset: Toolset,
    *,
    ctx: Optional[RunContext] = None,
    policy: Optional[ToolPolicy] = None,
    gate: Optional[Gate] = None,
    hooks: Optional[HookSet] = None,
    redact: Any = None,
    on_start: Optional[OnStart] = None,
    on_message: Optional[OnMessage] = None,
) -> List[Union[dict, Paused, None]]:
    """:func:`dispatch` for a run that can park a turn: per call, its
    message, a :class:`Paused`, or ``None`` for a sequential call that did
    not start because one before it paused. ``gate`` decides the calls
    that need approval (default: refuse them)."""
    policy = policy or DEFAULT_POLICY
    gate = gate or _approver_gate(None)
    base_ctx = ctx if ctx is not None else RunContext()
    normal = [normalize_tool_call(c) for c in calls]
    out: List[Union[dict, Paused, None]] = [None] * len(normal)
    common = (toolset, base_ctx, policy, gate, hooks, redact, on_start)

    async def run(index: int) -> None:
        out[index] = await _one(normal[index], *common)
        if on_message is not None and isinstance(out[index], dict):
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
    paused = any(isinstance(o, Paused) for o in out)
    for i in range(len(normal)):
        if out[i] is None and not paused:
            await run(i)
            paused = isinstance(out[i], Paused)
    return out


def _approver_gate(approve: Optional[Approver]) -> Gate:
    async def gate(call: ToolCall, spec: ToolSpec, reason: str) -> Optional[str]:
        if approve is None:
            return NO_APPROVER.format(name=call.name)
        asked = {"id": call.id, "name": call.name, "args": call.args}
        return None if await approve(asked, spec) else DENIED.format(name=call.name)

    return gate


@functools.lru_cache(maxsize=1024)
def _label(name: str) -> str:
    """The trace name of a call. The model chose ``name``, and a trace
    segment cannot hold ``.``, ``[``, ``]`` or ``#``."""
    return _UNSAFE.sub("_", name) or "tool"


def _sequential(t: Optional[Tool]) -> bool:
    # An unknown tool only produces a message; it may go with the batch.
    return t is not None and t.spec.sequential


async def _one(call, toolset, ctx, policy, gate, hooks, redact, on_start) -> Union[dict, Paused]:
    call_id, name, raw = call["id"], call["name"], call["args"]
    found = toolset.get(name)
    shown = redact.scrub_data(raw) if redact is not None else raw
    async with child(_label(name), inputs={"args": shown}, op_type="tool") as rec:
        rec.attrs.update(
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
                "gen_ai.tool.call.id": call_id,
            }
        )
        outcome = await _answer(call, found, toolset, ctx, policy, gate, hooks, on_start)
        if isinstance(outcome, Paused):
            rec.outputs = {"interrupted": [i.id for i in outcome.interruptions]}
        elif redact is not None:
            rec.outputs = {"tool_message": redact.scrub_message(outcome)}
        else:
            rec.outputs = {"tool_message": outcome}
        return outcome


async def _answer(
    call: Dict[str, Any],
    found: Optional[Tool],
    toolset: Toolset,
    ctx: RunContext,
    policy: ToolPolicy,
    gate: Gate,
    hooks: Optional[HookSet],
    on_start: Optional[OnStart],
) -> Union[dict, Paused]:
    call_id, name, raw = call["id"], call["name"], call["args"]

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
    if found.takes_context or not isinstance(found.spec.approval, str) or hooks:
        call_ctx = copy.copy(ctx)  # per call; deps and the meter are shared
        call_ctx.tool_call_id = call_id
    the_call = ToolCall(call_id, name, args)
    reason = f"the policy asks before running {name!r}" if decision == "ask" else ""
    if hooks is not None and hooks.before_tool:
        verdict = await _before_tool(hooks, call_ctx, the_call, found)
        if isinstance(verdict, Deny):
            return say(HOOK_DENIED.format(reason=_sentence(verdict.reason), name=name), error=True)
        if isinstance(verdict, Ask):
            decision = "ask"
            reason = verdict.reason or f"a hook asks before running {name!r}"
        elif verdict is not None:
            the_call, args = verdict, verdict.args
    if decision != "ask" and _needs_approval(found.spec, call_ctx, args):
        decision, reason = "ask", _approval_reason(found.spec, name)
    if decision == "ask":
        try:
            refusal = await gate(the_call, found.spec, reason)
        except Interrupted as pause:
            return Paused(call, pause.interruptions)
        if refusal is not None:
            return say(refusal, error=True)

    if on_start is not None:
        on_start({"id": call_id, "name": name, "args": args})
    error = True
    try:
        result = found.function(call_ctx, **args) if found.takes_context else found.function(**args)
        if inspect.isawaitable(result):
            timeout = found.spec.timeout
            result = await (asyncio.wait_for(result, timeout) if timeout else result)
        content, error = _render(result), False
    except asyncio.TimeoutError:
        content = TIMEOUT.format(name=name, timeout=found.spec.timeout)
    except ModelRetry as retry:
        content = retry.message
    except Interrupted as pause:
        # A tool that runs another agent: that agent's call waits on a human.
        return Paused(call, pause.interruptions)
    except Exception as exc:  # noqa: BLE001 - every failure must reach the model
        content = EXEC_ERROR.format(name=name, error=f"{type(exc).__name__}: {exc}")
    if hooks is not None and hooks.after_tool:
        # Before truncation: a redaction must see the whole text.
        content = await hooks.tool_content(call_ctx, the_call, content)
    return say(content, error=error)


async def _before_tool(hooks: HookSet, ctx: RunContext, call: ToolCall, found: Tool) -> Any:
    """The hooks' merged verdict: deny > ask > a replacement call > none.
    Each hook sees the previous one's replacement."""
    asked: Optional[Ask] = None
    replaced: Optional[ToolCall] = None
    for hook in hooks.before_tool:
        verdict = await hook.before_tool(ctx, call)
        if verdict is None:
            continue
        if isinstance(verdict, Deny):
            return verdict
        if isinstance(verdict, Ask):
            asked = asked or verdict
        elif isinstance(verdict, ToolCall):
            if verdict.name != call.name or verdict.id != call.id:
                raise ValueError(
                    f"{type(hook).__name__}.before_tool returned a call to {verdict.name!r} "
                    f"({verdict.id}) for {call.name!r} ({call.id}). A hook may change a call's "
                    "arguments (call.replace(args=...)), not which call it is."
                )
            call = replaced = ToolCall(call.id, call.name, found.validate(dict(verdict.args)))
        else:
            raise TypeError(
                f"{type(hook).__name__}.before_tool returned {type(verdict).__name__}; it "
                "returns None, a ToolCall (call.replace(args=...)), Ask(reason) or Deny(reason)."
            )
    return asked or replaced


def _sentence(text: str) -> str:
    text = (text or "a hook refused this call").strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _approval_reason(spec: ToolSpec, name: str) -> str:
    if spec.approval == "always":
        return f"{name!r} always asks for approval"
    return f"{name!r} asks for approval for these arguments"


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
