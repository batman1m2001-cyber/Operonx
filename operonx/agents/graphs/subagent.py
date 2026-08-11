"""Sub-agents — a nested ReAct loop with a narrower toolset.

A sub-agent is not a new mechanism. It is `build_react_agent` again, with
fewer tools, its own budget and its own conversation, invoked as a tool
by the parent. Nesting a `@graph` already gives isolated state, nested
trace spans and cancellation that propagates from the parent, so none of
that is written here.

**The toolset is enforced by a policy, not by omission.** An earlier
version computed the child's allowed names and then never passed them
anywhere — the child resolved against the global ``TOOL_REGISTRY`` and
happily ran tools its parent had excluded. `allow_tools` was decoration.
Now the names are compiled into a `ToolPolicy` that denies everything
else, which is the same mechanism the parent uses and the only one
`dispatch` actually consults.

There are still no capability tokens, so the construction site remains
the boundary: an author who passes the full registry has given the child
everything, and nothing downstream will object.

**Delegation does not nest by default.** A sub-agent that can delegate
can spawn a tree, and a model that has decided delegation is the answer
will keep deciding that. `max_depth` bounds it; the delegate tool is
removed from the child's toolset at depth 1 regardless.

The child's answer comes back as **text**, not as messages. A sub-agent
exists to spend context the parent does not have to hold; handing back
the full transcript would defeat the point.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

from operonx.agents.graphs.react import agent_result, build_react_agent
from operonx.agents.policy import DEFAULT_POLICY, ToolPolicy
from operonx.agents.redact import Redactor
from operonx.agents.tool import TOOL_REGISTRY, tool
from operonx.core.engine import Operon

__all__ = ["make_delegate_tool", "DELEGATE_SCHEMA", "NO_TOOLS_MESSAGE"]

#: Names never handed to a child. `delegate` is the recursion guard; the
#: rest touch the parent's own conversational surface, and a child
#: answering the user directly bypasses the parent that was asked.
DELEGATE_BLOCKED_TOOLS = frozenset({"delegate", "ask_user", "clarify", "send_message", "finish"})

DELEGATE_SCHEMA = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": (
                "A self-contained instruction. The sub-agent sees this and "
                "nothing else — no conversation history — so include every "
                "detail it needs."
            ),
        }
    },
    "required": ["task"],
}

NO_TOOLS_MESSAGE = (
    "Error: delegation is unavailable — the sub-agent would have no tools. Do the work directly."
)


def _child_tool_names(
    allow: Optional[Iterable[str]],
    depth: int,
    max_depth: int,
) -> List[str]:
    """Resolve the child's toolset.

    ``allow=None`` means "everything the parent has", which is the
    convenient default and the dangerous one — it is why the blocklist
    is applied unconditionally rather than only when a subset was named.
    """
    names = list(TOOL_REGISTRY) if allow is None else [n for n in allow if n in TOOL_REGISTRY]
    blocked = set(DELEGATE_BLOCKED_TOOLS)
    if depth + 1 >= max_depth:
        # Belt and braces: `delegate` is in the blocklist already, but a
        # caller who overrides the blocklist should still not get an
        # unbounded tree.
        blocked.add("delegate")
    return [n for n in names if n not in blocked]


def make_delegate_tool(
    *,
    call_model: Callable,
    name: str = "delegate",
    description: str = (
        "Hand a self-contained sub-task to a fresh agent with its own context "
        "budget. Use it for work that would otherwise fill this conversation — "
        "reading many files, exploring a large codebase. Returns only the "
        "sub-agent's final answer."
    ),
    allow_tools: Optional[Iterable[str]] = None,
    max_turns: int = 10,
    max_depth: int = 1,
    depth: int = 0,
    policy: Optional[ToolPolicy] = None,
    redactor: Optional[Redactor] = None,
    timeout: float = 300.0,
) -> Any:
    """Register a ``delegate`` tool that runs a sub-agent.

    Args:
        call_model: Model op factory for the child. Often a cheaper model
            than the parent's — sub-tasks are usually narrower.
        allow_tools: Names the child may use. ``None`` means everything
            currently registered, minus the blocklist. Naming a subset
            explicitly is the only real restriction, since the child's
            tools are fixed here and it cannot ask for more.
        max_turns: The child's own turn budget, separate from the
            parent's. A child that loops does not consume the parent's.
        max_depth: How deep delegation may nest. ``1`` means a
            sub-agent cannot delegate further, which is the default
            because a model that has decided delegation is the answer
            keeps deciding that.
        depth: Current depth. Set by the recursion, not by callers.
        policy / redactor: Applied to the *child's* tool calls. A child
            inherits nothing implicitly; anything unpassed is the
            default, which for policy means destructive tools still ask.
        timeout: Wall clock for one delegation.

    Returns:
        The registered tool factory.

    Raises:
        ValueError: if ``name`` is already registered — see
            :func:`~operonx.agents.tool.tool`.
    """

    @tool(name=name, description=description, schema=DELEGATE_SCHEMA, bound="io")
    async def delegate(task: str) -> dict:
        # Resolved per call, not at decoration. A construction-time
        # snapshot made the child's toolset depend on module import
        # order, and a tool registered later was invisible to it.
        child_names = _child_tool_names(allow_tools, depth, max_depth)
        if not child_names:
            # Better to say so than to spawn an agent that can only talk.
            return {"error": NO_TOOLS_MESSAGE}

        child = build_react_agent(
            call_model=call_model,
            max_turns=max_turns,
            policy=_child_policy(child_names, policy),
            redactor=redactor,
        )(messages=None)

        handle = Operon(child).start(inputs={"messages": [{"role": "user", "content": task}]})
        try:
            import asyncio

            await asyncio.wait_for(handle.result(), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - reported to the parent model
            return {
                "error": (
                    f"the sub-agent failed: {type(exc).__name__}: {exc}. "
                    f"Do this part yourself or narrow the task."
                )
            }

        result = agent_result(handle.state, child)
        final = result.get("final") or {}
        answer = (final.get("content") or "").strip()

        if not answer:
            # An empty answer means an op raised — operonx records the
            # error into state rather than propagating it — so say that
            # instead of returning a confident blank.
            return {
                "error": (
                    "the sub-agent produced no answer. It may have exhausted its "
                    "turn budget or hit an error; try a narrower task."
                )
            }

        return {
            "answer": answer,
            "turns": result.get("turns", 0),
            "truncated": bool(result.get("stopped_early")),
        }

    return delegate


def _child_policy(child_names: List[str], parent: Optional[ToolPolicy]) -> ToolPolicy:
    """Compile the allowed names into a policy that refuses the rest.

    Default-deny with an explicit per-name rule, rather than trusting the
    child to only ask for what it was given: the model chooses tool names
    freely, and dispatch resolves them against the process-wide registry.
    Omitting a tool from a list the child never sees restricts nothing.

    Allowed tools keep the **parent's** verdict, so a destructive tool
    that would have asked the parent still asks in the child rather than
    being silently promoted to `allow` by delegation.
    """
    base = parent or DEFAULT_POLICY
    rules = {}
    for tool_name in child_names:
        meta = getattr(TOOL_REGISTRY.get(tool_name), "_tool_meta", None) or {}
        rules[tool_name] = base.decide(tool_name, meta)
    return ToolPolicy(default="deny", destructive=None, readonly=None, rules=rules)


def describe_delegation(
    allow_tools: Optional[Iterable[str]] = None,
    max_depth: int = 1,
    depth: int = 0,
) -> Dict[str, Any]:
    """What a child would actually get. For tests and for a startup log.

    Worth logging at construction: the difference between "I restricted
    the sub-agent" and "I passed the whole registry" is invisible at
    runtime, and this is the only place it is knowable.
    """
    names = _child_tool_names(allow_tools, depth, max_depth)
    return {
        "tools": sorted(names),
        "blocked": sorted(set(TOOL_REGISTRY) - set(names)),
        "can_delegate_further": "delegate" in names,
    }
