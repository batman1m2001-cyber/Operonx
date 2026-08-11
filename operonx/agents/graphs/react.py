"""The ReAct loop — a ``@graph`` with a back-edge.

::

    START ─▶ count_turn ─▶ call_model ─▶ decide ─┬─▶ END
                                  └─▶ dispatch ──▶ back to call_model

The back-edge is what the Phase 3 cycle rewrite turns into a synthesized
loop at build time. Nothing here wraps or drives iteration.

**Reading the result.** ``Operon.run()`` reports a graph's declared
outputs as the *stream of writes* — with a loop that means one entry per
iteration, so ``result["messages"]`` is a list of per-turn lists. The
reducer-merged conversation lives in the state cell, so read the agent
through :func:`agent_result` rather than indexing the raw dict.

**The turn cap lives here, not in the graph.** ``@graph`` has no
``max_iterations`` and the synthesized loop's ceiling is a runaway guard
set far above any real workload. That guard is the wrong tool for a
budget anyway: it cuts mid-flight, the model is never told, and you keep
whatever partial state existed. ``count_turn`` injects a notice at the
limit instead and lets the model take one final turn, so exhaustion
exits the way success does.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from operonx.agents.graphs.dispatch import build_dispatch
from operonx.agents.policy import ToolPolicy
from operonx.agents.redact import Redactor
from operonx.core.ops.base import END, PARENT, START
from operonx.core.ops.flow.branch_op import if_
from operonx.core.ops.graph._decorators import graph
from operonx.core.ops.transform.func_op import op
from operonx.reducers import add_messages

__all__ = ["build_react_agent", "agent_result", "BUDGET_EXHAUSTED"]

BUDGET_EXHAUSTED = (
    "You have used your entire turn budget ({max_turns} turns) and cannot "
    "call any more tools. Answer now with what you already have, and say "
    "plainly what you could not finish."
)


# The graph input is named `messages`, the same as the shared cell, so
# operonx seeds the cell from it directly. An explicit seeding op wrote
# the opening messages a *second* time — invisible when they carry ids,
# because `add_messages` upserts on id, and a duplicated user turn
# otherwise.


@op
def gather_tool_messages(tool_messages: Optional[list] = None) -> dict:
    """Re-wrap collected tool results as one list for the reducer.

    ``Ref.collect()`` buffers per-call frames, but each frame carries a
    single message *dict*. ``add_messages`` takes lists on both sides and
    raises on a dict, so writing the raw frames blows up the reducer —
    and because operonx records op errors into state rather than raising
    (§15.1 V5), the run then ends quietly with a partial conversation.
    """
    if tool_messages is None:
        return {"messages": []}
    if isinstance(tool_messages, dict):
        # Single call: collect() hands back the frame itself, not a list.
        return {"messages": [tool_messages]}
    return {"messages": [m for m in tool_messages if isinstance(m, dict)]}


# There is deliberately no terminal `finish` op. The obvious design — an
# op after the loop that reads the cells and emits them — never runs: a
# loop containing a generator emits at *item* contexts, so a downstream
# op never reaches ready and is skipped with no error. The conversation
# lives in the shared cells regardless, so `agent_result` reads those.


def build_react_agent(
    *,
    call_model: Callable,
    max_turns: int = 25,
    approval_timeout: float = 300.0,
    policy: Optional[ToolPolicy] = None,
    redactor: Optional[Redactor] = None,
    budget_notice: str = BUDGET_EXHAUSTED,
):
    """Build the ReAct agent graph.

    Args:
        call_model: An op factory taking ``messages: list`` and returning
            ``assistant_message`` (a list of message dicts), ``tool_calls``
            (a list, empty when finished) and ``done`` (bool). Injected
            rather than constructed here so the loop is testable without a
            provider, and so callers choose their own ``LLMOp.of(...)``
            configuration.
        max_turns: Turn budget. One turn is one model call plus the
            dispatch of whatever tools it asked for. Reaching it is a
            normal exit, not a cut: the model is given ``budget_notice``
            and one final turn to answer.
        approval_timeout: Seconds a gated tool call waits for a human
            before being denied. See :mod:`operonx.agents.graphs.dispatch`.
        policy: Which tools may run, ask, or are refused outright. See
            :class:`~operonx.agents.policy.ToolPolicy`. Defaults to
            destructive-asks, everything-else-runs.
        redactor: Strips credential-shaped strings from tool output
            before the model or the tracer sees it. See
            :class:`~operonx.agents.redact.Redactor`.
        budget_notice: Message appended when the budget runs out. Must
            tell the model to answer now — an empty or vague notice
            produces another tool call it is not allowed to make.

    Returns:
        A ``@graph`` factory. Call it with ``messages=`` to build.
    """
    if max_turns < 1:
        raise ValueError(f"max_turns must be >= 1, got {max_turns}. One turn is one model call.")

    dispatch_one = build_dispatch(
        approval_timeout=approval_timeout, policy=policy, redactor=redactor
    )

    @op
    def count_turn(turns: int = 0) -> dict:
        """Advance the turn counter and decide whether this is the last one.

        Separate from ``call_model`` so the budget is enforced regardless of
        which model op a caller injects — a budget the caller could forget
        to implement is not a budget.
        """
        turns = (turns or 0) + 1
        exhausted = turns >= max_turns
        return {
            "turns": turns,
            "notice": [{"role": "user", "content": budget_notice.format(max_turns=max_turns)}]
            if exhausted
            else [],
            "exhausted": exhausted,
        }

    @op
    def decide(
        done: bool = False, exhausted: bool = False, tool_calls: Optional[list] = None
    ) -> dict:
        """Fold the three exit conditions into one branch input.

        The model is finished, the budget is spent, or it asked for no
        tools — any of those ends the loop. Computing it in an op rather
        than in the branch expression keeps it a Ref-vs-literal
        comparison, which is the only form ``if_`` evaluates correctly.
        """
        finished = bool(done) or bool(exhausted) or not (tool_calls or [])
        return {"finished": finished}

    @graph
    def react(messages=None):
        PARENT.declare(
            messages=[],
            turns=0,
            stopped_early=False,
            reducers={"messages": add_messages},
        )

        counter = count_turn(turns=PARENT["turns"])
        model = call_model(messages=PARENT["messages"])
        router = decide(
            done=model["done"],
            exhausted=counter["exhausted"],
            tool_calls=model["tool_calls"],
        )

        calls = each_call_of(tool_calls=model["tool_calls"])
        disp = dispatch_one(call=calls["call"].parallel(max=8))
        gathered = gather_tool_messages(tool_messages=disp["tool_message"].collect())

        # Accumulate into the shared cell. The reducer merges by id, so a
        # re-emitted message updates rather than duplicating.
        counter["turns"] >> PARENT["turns"]
        counter["notice"] >> PARENT["messages"]
        counter["exhausted"] >> PARENT["stopped_early"]
        model["assistant_message"] >> PARENT["messages"]
        gathered["messages"] >> PARENT["messages"]

        START >> counter >> model >> router
        router >> if_(router["finished"] == True, END).else_(calls)  # noqa: E712
        calls >> disp >> gathered >> counter  # back-edge — rewritten into a loop

    return react


@op
def each_call_of(tool_calls: Optional[list] = None):
    """Generator — one frame per tool call, so dispatch fans out.

    Defined at module scope rather than inside the factory so repeated
    ``build_react_agent`` calls share one op definition.
    """
    for index, call in enumerate(tool_calls or []):
        yield {"call": call, "index": index}


EMPTY_RESULT: dict[str, Any] = {
    "messages": [],
    "turns": 0,
    "stopped_early": False,
    "final": None,
}


def agent_result(source: Any, agent) -> dict:
    """Read the agent's answer out of a finished run.

    Args:
        source: Either what ``Operon.run()`` returned (its ``"$state"``
            entry is used) or a ``MemoryState`` directly. The HITL path
            goes through ``engine.start()``, and ``handle.result()``
            builds its dict from emitted frames only — it carries no
            state — so those callers pass ``handle.state``.
        agent: The built graph that produced it — the same object handed
            to ``Operon(...)``. Needed because the answer lives in that
            graph's shared cells.

    Always read the agent through this. Indexing the run dict directly
    gives the *stream of writes*: one entry per loop iteration, so
    ``result["messages"]`` is a list of per-turn lists rather than the
    conversation. The reducer-merged value is in the cell.

    Returns :data:`EMPTY_RESULT`'s shape when nothing was produced, so
    callers can read ``["messages"]`` unconditionally. An empty answer
    means an op raised — operonx records errors into state and returns a
    partial result rather than propagating — so check the logs rather
    than concluding the agent had nothing to say.
    """
    state = source.get("$state") if isinstance(source, dict) else source
    if state is None or not hasattr(state, "schema"):
        return dict(EMPTY_RESULT)

    def cell(var, default):
        try:
            value = state[agent.full_name, var]
        except Exception:  # noqa: BLE001 - absent cell is "nothing ran"
            return default
        return default if value is None else value

    messages = cell("messages", [])
    if not isinstance(messages, list):
        messages = []
    return {
        "messages": messages,
        "turns": cell("turns", 0),
        "stopped_early": bool(cell("stopped_early", False)),
        "final": next(
            (m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "assistant"),
            None,
        ),
    }
