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

**The child is shown only what it may call.** Restricting dispatch is not
enough: the child's model is told about tools through ``call_model``'s
``tools=``, and a caller from ``make_llm_caller`` had the parent's whole
list baked in. A model shown a tool it will always be refused calls it,
and spends its own budget on refusals. The child's caller is narrowed
with ``call_model.with_tools(...)`` to the tools its policy allows.

**A tool that needs a human is refused in a child, at once.** The child
is a separate run with its own state, and an approval is answered on the
state that raised it; the caller's approver holds only the parent's.
Nothing reaches the child's interrupt, so an inherited ``ask`` used to
wait out the whole ``approval_timeout`` — as long as the delegation's own
timeout by default — and the parent heard only "timed out". Bridging the
two runs' interrupt buses would mean reaching into core plumbing from
here, to let a human approve a call they cannot see the context of. So
``ask`` becomes a refusal that says why, the tool is not shown to the
child, and the parent — where approval works — can do that step itself.

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

__all__ = ["make_delegate_tool", "DELEGATE_SCHEMA", "NO_TOOLS_MESSAGE", "NEEDS_A_HUMAN_MESSAGE"]

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

#: What a child is told when it calls a tool that would ask a human.
NEEDS_A_HUMAN_MESSAGE = (
    "Blocked: {name!r} needs a human's approval, and a sub-agent has no one "
    "to ask. Do not retry it. Finish what you can, and say in your answer "
    "that this step has to be done by the agent that delegated to you."
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
            than the parent's — sub-tasks are usually narrower. If it has
            ``with_tools(names)`` — a ``make_llm_caller`` caller does — the
            child's model is shown only the tools its policy allows. A
            hand-written factory without it must do that itself; the
            child's policy still refuses the rest either way.
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
            default. A tool the policy would ``ask`` about is refused in
            the child (see the module docstring) — so under the default
            policy a child never runs a destructive tool.
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
        child_policy = _child_policy(child_names, policy)
        shown = _shown(child_names, child_policy)
        if not shown:
            # Better to say so than to spawn an agent that can only talk.
            return {"error": NO_TOOLS_MESSAGE}

        narrow = getattr(call_model, "with_tools", None)
        child = build_react_agent(
            call_model=narrow(shown) if callable(narrow) else call_model,
            max_turns=max_turns,
            policy=child_policy,
            redactor=redactor,
        )(messages=None)

        handle = Operon(child).start(inputs={"messages": [{"role": "user", "content": task}]})
        try:
            import asyncio

            await asyncio.wait_for(handle.result(), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - reported to the parent model
            # `wait_for` cancels only the waiter. Left running, the child
            # would keep calling tools after the parent was told it failed.
            handle.cancel()
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


class _ChildPolicy(ToolPolicy):
    """A child's policy: default-deny, and a refusal that says *why* for a
    tool that was denied only because it needs a human."""

    __slots__ = ("needs_a_human",)

    def __init__(self, rules: Dict[str, str], needs_a_human: Iterable[str]) -> None:
        super().__init__(default="deny", destructive=None, readonly=None, rules=rules)
        self.needs_a_human = frozenset(needs_a_human)

    def refusal(self, name: str) -> str:
        if name in self.needs_a_human:
            return NEEDS_A_HUMAN_MESSAGE.format(name=name)
        return super().refusal(name)


def _meta(tool_name: str) -> dict:
    return getattr(TOOL_REGISTRY.get(tool_name), "_tool_meta", None) or {}


def _child_policy(child_names: List[str], parent: Optional[ToolPolicy]) -> ToolPolicy:
    """Compile the allowed names into a policy that refuses the rest.

    Default-deny with an explicit per-name rule, rather than trusting the
    child to only ask for what it was given: the model chooses tool names
    freely, and dispatch resolves them against the process-wide registry.
    Omitting a tool from a list the child never sees restricts nothing.

    Allowed tools keep the **parent's** verdict, so a destructive tool is
    never silently promoted to `allow` by delegation — except that `ask`
    becomes a refusal: nothing can answer an approval raised inside a
    child (see the module docstring), and waiting for one only stalls.
    """
    base = parent or DEFAULT_POLICY
    rules: Dict[str, str] = {}
    needs_a_human = []
    for tool_name in child_names:
        verdict = base.decide(tool_name, _meta(tool_name))
        if verdict == "ask":
            needs_a_human.append(tool_name)
            verdict = "deny"
        rules[tool_name] = verdict
    return _ChildPolicy(rules=rules, needs_a_human=needs_a_human)


def _shown(child_names: List[str], child_policy: ToolPolicy) -> List[str]:
    """The tools a child's model is told about: those its policy allows.

    A tool it will always be refused is not an option, and listing it
    only invites a call that burns a turn on the refusal.
    """
    return [n for n in child_names if child_policy.decide(n, _meta(n)) == "allow"]


def describe_delegation(
    allow_tools: Optional[Iterable[str]] = None,
    max_depth: int = 1,
    depth: int = 0,
    policy: Optional[ToolPolicy] = None,
) -> Dict[str, Any]:
    """What a child would actually get. For tests and for a startup log.

    Worth logging at construction: the difference between "I restricted
    the sub-agent" and "I passed the whole registry" is invisible at
    runtime, and this is the only place it is knowable.

    ``tools`` is the toolset after ``allow_tools`` and the blocklist;
    ``shown`` is what the child's model is told about and may call, once
    ``policy`` has refused what it denies or would ask a human about.
    """
    names = _child_tool_names(allow_tools, depth, max_depth)
    return {
        "tools": sorted(names),
        "shown": sorted(_shown(names, _child_policy(names, policy))),
        "blocked": sorted(set(TOOL_REGISTRY) - set(names)),
        "can_delegate_further": "delegate" in names,
    }
