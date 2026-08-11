"""Tool dispatch — one subgraph per tool call, fanned out.

Shape (4 ops + 1 branch per call)::

    parse ──▶ route ──▶ approve (InterruptOp) ──┐
                   └──▶ auto_ok ────────────────┴──▶ execute ──▶ END
                                    (both write PARENT["decision"])

Three properties this file exists to guarantee, each learned the hard
way — see AGENT_EXTENSION_PLAN.md §15.1:

**Every call produces exactly one tool message.** Providers reject a
conversation where an assistant ``tool_call`` has no matching tool
result, so *every* failure mode — unknown tool, bad arguments, an
exception inside the tool, a timeout, a human denial — has to come back
as a message the model can read. ``execute`` therefore catches
everything. It has to catch it *itself*: operonx records op exceptions
into state and returns a partial result rather than raising (V5), so an
uncaught error here would produce no message at all and the failure
would surface one turn later as a provider 400.

**The two approval arms converge on one ``execute``.** Both write
``PARENT["decision"]`` and ``execute`` reads the cell, so approval logic
stays out of the execution path and adding a policy arm later costs one
node rather than a second copy of everything downstream (V1).

**The approval payload is built upstream.** A ``Ref`` nested in a dict is
rejected at construction, so ``parse`` assembles the payload and the
``InterruptOp`` takes a single bare ref. Before that check existed, the
human approving a destructive call was shown ``Ref`` objects instead of
the tool name and arguments (V2).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from operonx.agents.policy import DEFAULT_POLICY, DENY_MESSAGE, ToolPolicy
from operonx.agents.tool import TOOL_REGISTRY
from operonx.core.ops.base import END, START
from operonx.core.ops.flow.branch_op import if_
from operonx.core.ops.flow.interrupt_op import InterruptOp
from operonx.core.ops.graph._decorators import graph
from operonx.core.ops.transform.func_op import op

__all__ = [
    "each_call",
    "parse_call",
    "auto_approve",
    "normalize_decision",
    "execute",
    "build_dispatch",
]

# Mirrors langgraph's TOOL_CALL_ERROR_TEMPLATE: the model recovers far
# better from a named error than from a silent absence.
_UNKNOWN_TOOL = (
    "Error: no tool named {name!r}. Available tools: {available}. "
    "Call one of those, or answer without a tool."
)
_ARGS_ERROR = "Error: could not parse arguments for {name!r}: {error}. Expected a JSON object."
_EXEC_ERROR = "Error: tool {name!r} failed: {error}"
_TIMEOUT = "Error: tool {name!r} timed out after {timeout}s."
_DENIED = "Blocked: a human declined this {name!r} call. Do not retry it; ask how to proceed."
_TIMED_OUT_APPROVAL = (
    "Blocked: the approval request for {name!r} expired with no answer. "
    "Treat it as declined and ask how to proceed."
)


def _tool_message(call_id: str, name: str, content: str, *, is_error: bool = False) -> dict:
    """The one shape every dispatch path returns."""
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": content,
        "status": "error" if is_error else "success",
    }


def _stringify(result: Any, limit: int) -> str:
    """Render a tool result for the model, truncating to ``limit``.

    Truncation is announced rather than silent — a model shown a
    half-file with no marker will reason about it as if it were whole.
    """
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(result)
    if limit and len(text) > limit:
        omitted = len(text) - limit
        return f"{text[:limit]}\n\n[truncated: {omitted} more characters]"
    return text


@op
def each_call(tool_calls: Optional[list] = None):
    """Generator — one frame per tool call.

    Yielding rather than looping is what buys per-call concurrency,
    per-call trace spans and per-call ctx isolation downstream
    (plan §6 Rule 1). An empty list yields nothing, so a turn with no
    tool calls costs no dispatch work.
    """
    for index, call in enumerate(tool_calls or []):
        yield {"call": call, "index": index}


@op
def parse_call(call: Optional[dict] = None, policy: Any = None) -> dict:
    """Validate a tool call against the registry and policy, and shape
    the payloads.

    Never raises. An unknown tool, unparseable arguments, or a policy
    refusal all become ``error``, which ``execute`` turns into a tool
    message — the model can correct itself from that, but not from a
    traceback.
    """
    call = call or {}
    call_id = call.get("id") or call.get("tool_call_id") or ""
    name = call.get("name") or (call.get("function") or {}).get("name") or ""

    raw_args = call.get("args")
    if raw_args is None:
        raw_args = (call.get("function") or {}).get("arguments")

    error = ""
    args: Dict[str, Any] = {}
    if isinstance(raw_args, dict):
        args = raw_args
    elif isinstance(raw_args, str) and raw_args.strip():
        try:
            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                args = parsed
            else:
                error = _ARGS_ERROR.format(name=name, error=f"got a JSON {type(parsed).__name__}")
        except json.JSONDecodeError as exc:
            error = _ARGS_ERROR.format(name=name, error=str(exc))
    elif raw_args not in (None, ""):
        error = _ARGS_ERROR.format(name=name, error=f"got {type(raw_args).__name__}")

    factory = TOOL_REGISTRY.get(name)
    meta = getattr(factory, "_tool_meta", None) or {}

    # Policy decides allow / ask / deny from what the tool declares. A
    # refusal is folded into `error` rather than routed through the gate:
    # asking a human to approve something policy already forbids trains
    # them to click through, and their answer would be ignored.
    decision = (policy or DEFAULT_POLICY).decide(name, meta)

    # Policy is evaluated **before** the unknown-tool check, so a rule
    # like ``rules={"shell": "deny"}`` still refuses when that tool is
    # not loaded. Reporting "no tool named 'shell'" instead would tell
    # the model the capability is merely absent and invite it to look
    # for another route to the same thing.
    if decision == "deny" and not error:
        error = DENY_MESSAGE.format(name=name)
    elif factory is None and not error:
        error = _UNKNOWN_TOOL.format(
            name=name, available=", ".join(sorted(TOOL_REGISTRY)) or "(none registered)"
        )

    # An unknown, denied or malformed call must not reach the approval
    # gate — it cannot execute, so there is nothing to approve.
    needs_approval = decision == "ask" and not error

    # Built here, deliberately: a Ref nested in a dict is rejected at
    # construction, so the InterruptOp downstream takes one bare ref.
    approval_payload = {
        "tool": name,
        "args": args,
        "call_id": call_id,
        "description": meta.get("description", ""),
    }

    return {
        "tool_name": name,
        "args": args,
        "call_id": call_id,
        "error": error,
        "needs_approval": needs_approval,
        "approval_payload": approval_payload,
        "max_result_chars": int(meta.get("max_result_chars") or 100_000),
        "timeout": meta.get("timeout"),
    }


@op
def auto_approve() -> dict:
    """The non-destructive arm. Exists so both arms write the same cell
    and ``execute`` stays single — see the module docstring."""
    return {"decision": {"approved": True, "auto": True}}


@op
def normalize_decision(response: Any = None, timed_out: bool = False) -> dict:
    """Fold ``InterruptOp``'s two outputs into the one cell ``execute`` reads.

    ``InterruptOp`` reports expiry on a separate ``timed_out`` output and
    leaves ``response`` as ``None``. Writing only ``response`` to the cell
    would make an expired request indistinguishable from an explicit
    denial — both land as "not approved" — and the model would be told a
    human refused when nobody actually answered. Those warrant different
    guidance, so the distinction is preserved here.
    """
    if timed_out:
        return {"decision": {"approved": False, "timed_out": True}}
    if isinstance(response, dict):
        return {"decision": {**response, "approved": bool(response.get("approved", False))}}
    # A bare truthy/falsey answer from a terse caller.
    return {"decision": {"approved": bool(response)}}


@op(bound="io")
async def execute(
    tool_name: str = "",
    args: Optional[dict] = None,
    call_id: str = "",
    error: str = "",
    decision: Optional[dict] = None,
    max_result_chars: int = 100_000,
    timeout: Optional[float] = None,
) -> dict:
    """Run one tool call and return exactly one tool message.

    Catches every exception on purpose. operonx records op errors into
    state and returns a partial result rather than propagating (V5), so
    letting anything escape here would drop the tool message entirely
    and the provider would reject the *next* request — a failure that
    surfaces one turn away from its cause.
    """
    if error:
        return {"tool_message": _tool_message(call_id, tool_name, error, is_error=True)}

    decision = decision or {}
    if not decision.get("approved", False):
        template = _TIMED_OUT_APPROVAL if decision.get("timed_out") else _DENIED
        reason = decision.get("reason") or template.format(name=tool_name)
        return {"tool_message": _tool_message(call_id, tool_name, reason, is_error=True)}

    factory = TOOL_REGISTRY.get(tool_name)
    if factory is None:
        # Registry mutated between parse and execute — vanishingly rare,
        # but returning a message beats an AttributeError that produces none.
        content = _UNKNOWN_TOOL.format(
            name=tool_name, available=", ".join(sorted(TOOL_REGISTRY)) or "(none registered)"
        )
        return {"tool_message": _tool_message(call_id, tool_name, content, is_error=True)}

    try:
        call = factory(**(args or {}))
        # Reuse the op's own execution path so the tool keeps its tracing,
        # timing and bound routing rather than being called as a raw fn.
        coro = call.core(**(args or {}))
        raw = await asyncio.wait_for(coro, timeout) if timeout else await coro
    except asyncio.TimeoutError:
        content = _TIMEOUT.format(name=tool_name, timeout=timeout)
        return {"tool_message": _tool_message(call_id, tool_name, content, is_error=True)}
    except Exception as exc:  # noqa: BLE001 - every failure must reach the model
        content = _EXEC_ERROR.format(name=tool_name, error=f"{type(exc).__name__}: {exc}")
        return {"tool_message": _tool_message(call_id, tool_name, content, is_error=True)}

    return {"tool_message": _tool_message(call_id, tool_name, _stringify(raw, max_result_chars))}


def build_dispatch(*, approval_timeout: float = 300.0, policy: Optional[ToolPolicy] = None):
    """Return the per-call dispatch subgraph.

    Args:
        approval_timeout: Seconds a call waits for a human. On expiry the
            call is **denied**, not executed — a gate that fails open is
            decoration. The model is told it expired so it can ask rather
            than silently retry.
        policy: Which tools may run, ask, or are refused outright.
            Defaults to :data:`~operonx.agents.policy.DEFAULT_POLICY` —
            destructive tools ask, everything else runs.
    """

    @graph
    def dispatch_one(call):
        from operonx.core.ops._edges import PARENT

        # Both approval arms land here; `execute` reads the cell. Adding a
        # policy arm later costs one node, not a second execution path.
        PARENT.declare(decision=None)

        parsed = parse_call(call=call, policy=policy or DEFAULT_POLICY)

        approve = InterruptOp(
            payload=parsed["approval_payload"],  # bare ref — see module docstring
            timeout=approval_timeout,
        )
        # InterruptOp reports expiry on a separate output; fold both into
        # the single shape the cell carries.
        decided = normalize_decision(
            response=approve["response"],
            timed_out=approve["timed_out"],
        )
        auto = auto_approve()

        decided["decision"] >> PARENT["decision"]
        auto["decision"] >> PARENT["decision"]

        run = execute(
            tool_name=parsed["tool_name"],
            args=parsed["args"],
            call_id=parsed["call_id"],
            error=parsed["error"],
            decision=PARENT["decision"],
            max_result_chars=parsed["max_result_chars"],
            timeout=parsed["timeout"],
        )

        START >> parsed
        parsed >> if_(parsed["needs_approval"] == True, approve).else_(auto)  # noqa: E712
        approve >> decided >> run
        auto >> run
        run >> END

    return dispatch_one
