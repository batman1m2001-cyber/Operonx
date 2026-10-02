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
exits the way success does. That turn is called with tools disabled
(``last_turn=True``), because a notice is only prose and a model can
ignore it.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from operonx.agents.graphs.dispatch import build_dispatch, call_identity, tool_message
from operonx.agents.ops.compact_ops import (
    apply_compaction,
    estimate_tool_tokens,
    plan_compaction,
)
from operonx.agents.ops.memory_ops import gather_memory
from operonx.agents.ops.model_ops import TRUNCATED_REASONS
from operonx.core.utils.auto_name import register_skip
from operonx.agents.ops.prompt_ops import apply_cache_control, assemble_api_messages
from operonx.agents.policy import ToolPolicy
from operonx.agents.redact import Redactor
from operonx.agents.skills import inject_skills
from operonx.core.ops.base import END, PARENT, START
from operonx.core.ops.flow.branch_op import if_
from operonx.core.ops.graph._decorators import graph
from operonx.core.ops.transform.func_op import op
from operonx.reducers import add_messages

__all__ = ["build_react_agent", "agent_result", "BUDGET_EXHAUSTED", "NOT_RUN"]

BUDGET_EXHAUSTED = (
    "You have used your entire turn budget ({max_turns} turns) and cannot "
    "call any more tools. Answer now with what you already have, and say "
    "plainly what you could not finish."
)

#: The tool result given to a call the loop ends without dispatching.
NOT_RUN = {
    "budget": (
        "Not run: the turn budget ({max_turns} turns) ran out before this "
        "call could be dispatched. Do not assume it happened."
    ),
    "done": (
        "Not run: the turn was marked finished before this call could be "
        "dispatched. Do not assume it happened."
    ),
}


# The graph input is named `messages`, the same as the shared cell, so
# operonx seeds the cell from it directly. An explicit seeding op wrote
# the opening messages a *second* time — invisible when they carry ids,
# because `add_messages` upserts on id, and a duplicated user turn
# otherwise.


@op
def normalize_messages(messages: Any = None) -> dict:
    """Coerce a message write into the list ``add_messages`` requires.

    A subgraph's declared outputs are emitted per frame, and a frame that
    has not written a given var carries ``None`` for it. Writing that
    straight into a reducer cell raises ``add_messages expects list, got
    NoneType`` — and since operonx records op errors into state rather
    than propagating them, the run then ends quietly mid-conversation.
    Found against a live model on the second turn, not by any unit test.
    """
    if messages is None:
        return {"messages": []}
    if isinstance(messages, dict):
        return {"messages": [messages]}
    if isinstance(messages, list):
        return {"messages": [m for m in messages if isinstance(m, dict)]}
    return {"messages": []}


@op
def last_user_text(messages: Optional[list] = None) -> dict:
    """The most recent user turn, for memory and skill matching.

    Both retrieve against "what was asked", which is the last user
    message rather than the whole transcript — matching on the transcript
    makes every turn retrieve the same thing regardless of the question.
    The budget notice is skipped: it is framework text, not a query.
    """
    for message in reversed(messages or []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            if content.startswith("You have used your entire turn budget"):
                continue
            return {"text": content}
    return {"text": ""}


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
    system: str = "",
    memory_providers: Optional[list] = None,
    skills: Optional[list] = None,
    token_budget: int = 100_000,
    keep_recent: int = 6,
    cache_breakpoints: int = 1,
    budget_notice: str = BUDGET_EXHAUSTED,
):
    """Build the ReAct agent graph.

    Args:
        call_model: An op factory taking ``messages: list`` and returning
            ``assistant_message`` (a list of message dicts), ``tool_calls``
            (a list, empty when finished) and ``done`` (bool), and
            optionally ``finish_reason``/``truncated``, which
            :func:`agent_result` reports. A ``call_model`` that declares a
            ``last_turn`` parameter is passed ``last_turn=True`` on the
            budget's final turn and must not let the model call a tool
            then — ``make_llm_caller`` sends ``tool_choice="none"``. One
            without the parameter is called as before. Injected
            rather than constructed here so the loop is testable without a
            provider, and so callers choose their own ``LLMOp.of(...)``
            configuration.
        max_turns: Turn budget. One turn is one model call plus the
            dispatch of whatever tools it asked for. Reaching it is a
            normal exit, not a cut: the model is given ``budget_notice``
            and one final turn to answer, called with tools disabled when
            ``call_model`` takes ``last_turn`` (see above).
        approval_timeout: Seconds a gated tool call waits for a human
            before being denied. See :mod:`operonx.agents.graphs.dispatch`.
        policy: Which tools may run, ask, or are refused outright. See
            :class:`~operonx.agents.policy.ToolPolicy`. Defaults to
            destructive-asks, everything-else-runs.
        redactor: Strips credential-shaped strings from tool output
            before the model or the tracer sees it, and from the
            arguments in an approval request. See
            :class:`~operonx.agents.redact.Redactor`.
        system: System prompt. Sent first and never changed, which is
            what lets a provider cache the prefix.
        memory_providers: :class:`~operonx.agents.memory.MemoryProvider`
            instances consulted each turn. Retrieved context is placed
            *after* the conversation, not in the system prompt, because
            it changes per query and leading with it would push the whole
            history out of the cached prefix.
        skills: Loaded :class:`~operonx.agents.skills.Skill` objects.
            Matched per turn and injected as a user message, same
            placement and same reason as memory.
        token_budget: Prompt budget. Compaction triggers at 75% of it,
            not at 100% — the turn that discovers the budget is exceeded
            has already failed. The tool definitions ``call_model`` sends
            count against it: they are read from ``call_model.tools``,
            which a ``make_llm_caller`` caller carries and a hand-written
            factory can set.
        keep_recent: Exchanges kept verbatim by compaction. Recency is
            what the model is reasoning about; summarising it is how a
            compactor makes an agent forget what it just did.
        cache_breakpoints: Cache markers placed on the stable prefix, at
            most four (Anthropic rejects more). The Anthropic backend
            sends them as content-block ``cache_control``; OpenAI-shaped
            backends drop them, since that API caches prefixes itself.
        budget_notice: Message appended when the turn budget runs out.
            Must tell the model to answer now — an empty or vague notice
            produces another tool call it is not allowed to make.

    Returns:
        A ``@graph`` factory. Call it with ``messages=`` to build.
    """
    if max_turns < 1:
        raise ValueError(f"max_turns must be >= 1, got {max_turns}. One turn is one model call.")

    dispatch_one = build_dispatch(
        approval_timeout=approval_timeout, policy=policy, redactor=redactor
    )

    # The tool definitions ride on every request but are not messages, so
    # they are counted here, once, rather than passed to the planner each
    # turn. Leaving them out meant compacting against a budget the
    # request was already 2–4k tokens over with a typical registry.
    tool_tokens = estimate_tool_tokens(getattr(call_model, "tools", None))
    takes_last_turn = _takes_last_turn(call_model)

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

    @op
    def close_unrun_calls(
        finished: bool = False, exhausted: bool = False, messages: Optional[list] = None
    ) -> dict:
        """Answer every tool call the loop is about to abandon.

        The assistant message is written to the history *before* the
        branch decides whether to dispatch, so when the loop ends with
        calls still pending — the model ignored the budget notice, or a
        ``call_model`` said ``done`` while asking for a tool — the history
        ends on an unanswered ``tool_call``. Every provider rejects that,
        but only on the *next* request, one exchange after the cause.

        Answering each call with a "not run" result, rather than dropping
        the ``tool_calls`` from the stored message, is what keeps the
        history valid everywhere: the model's own turn is kept as it was
        sent, an assistant message is never left with empty content
        (Anthropic rejects that), and the model is told plainly that the
        calls did not happen instead of finding them silently gone.
        """
        if not finished:
            return {"messages": []}
        # The calls answered are the ones in the *stored* message, not the
        # model's `tool_calls` output: what the provider will see is the
        # history, and a result for a call the history does not hold is
        # an orphan it rejects just the same.
        pending = [
            call
            for message in messages or []
            if isinstance(message, dict)
            for call in message.get("tool_calls") or []
            if isinstance(call, dict)
        ]
        text = (NOT_RUN["budget"] if exhausted else NOT_RUN["done"]).format(max_turns=max_turns)
        return {
            "messages": [
                tool_message(*call_identity(call), text, is_error=True) for call in pending
            ]
        }

    @op
    def how_it_ended(
        exhausted: bool = False, finish_reason: str = "", truncated: bool = False
    ) -> dict:
        """Record how this turn ended; the last turn's write is the answer's.

        A response cut at ``length`` has no tool calls, so the loop treats
        it as done — correctly, since there is nothing to dispatch — but it
        is not a finished answer, and nothing downstream read the adapter's
        ``finish_reason``/``truncated``, so it was reported as a clean one.
        The flag is recomputed from the reason as well, for a hand-written
        ``call_model`` that reports only the reason. A ``call_model`` that
        reports neither reads as a clean stop.
        """
        reason = finish_reason or ""
        cut = bool(truncated) or reason in TRUNCATED_REASONS
        return {
            "stopped_early": bool(exhausted) or cut,
            "truncated": cut,
            "finish_reason": reason,
        }

    # ── zones ────────────────────────────────────────────────────────
    # The turn is three stages, each a subgraph, so a viewer shows the
    # loop's shape — context → model → tools → back — and a stage's steps
    # one level deeper, instead of fifteen nodes in a row.

    @graph
    def build_context(messages=None, query=None):
        """What the model sees this turn: compaction, memory, skills, the
        assembled prompt and its cache marks. Compaction shapes the *prompt*,
        not the stored conversation: the history stays whole, so nothing is
        lost and `agent_result` still returns everything that happened."""
        planned = plan_compaction(
            messages=messages,
            budget=token_budget,
            keep_recent=keep_recent,
            reserved_tokens=tool_tokens,
        )
        compacted = apply_compaction(
            pinned=planned["pinned"],
            summarize=planned["summarize"],
            keep=planned["keep"],
        )
        recalled = gather_memory(providers=memory_providers, query=query)
        matched = inject_skills(query=query, skills=skills)
        assembled = assemble_api_messages(
            system=system,
            messages=compacted["messages"],
            memory_context=recalled["context"],
            notices=matched["notices"],
        )
        cached = apply_cache_control(
            messages=assembled["messages"],
            breakpoints=cache_breakpoints,
        )
        START >> planned >> compacted >> recalled >> matched >> assembled >> cached >> END

    @graph
    def run_tools(tool_calls=None):
        """Every tool the model asked for, at once: one tool message per call.
        The loop gathers them (a subgraph is a one-EOF source for `.collect()`;
        a collect inside the subgraph would hand its result up twice)."""
        calls = each_call_of(tool_calls=tool_calls)
        disp = dispatch_one(call=calls["call"].parallel(max=8))
        START >> calls >> disp >> END

    @graph
    def react(messages=None):
        PARENT.declare(
            messages=[],
            turns=0,
            stopped_early=False,
            truncated=False,
            finish_reason="",
            reducers={"messages": add_messages},
        )

        counter = count_turn(turns=PARENT["turns"])
        asked = last_user_text(messages=PARENT["messages"])

        context = build_context(messages=PARENT["messages"], query=asked["text"])
        context.show_keys = ("messages",)  # what the model will see, not the last step's flag

        if takes_last_turn:
            model = call_model(messages=context["messages"], last_turn=counter["exhausted"])
        else:
            model = call_model(messages=context["messages"])
        router = decide(
            done=model["done"],
            exhausted=counter["exhausted"],
            tool_calls=model["tool_calls"],
        )
        assistant = normalize_messages(messages=model["assistant_message"])
        closed = close_unrun_calls(
            finished=router["finished"],
            exhausted=counter["exhausted"],
            messages=assistant["messages"],
        )
        # `finish_reason`/`truncated` are optional outputs of `call_model`:
        # one that does not produce them leaves these inputs at their
        # defaults.
        ended = how_it_ended(
            exhausted=counter["exhausted"],
            finish_reason=model["finish_reason"],
            truncated=model["truncated"],
        )
        tools = run_tools(tool_calls=model["tool_calls"])
        gathered = gather_tool_messages(tool_messages=tools["tool_message"].collect())

        # Accumulate into the shared cell. The reducer merges by id, so a
        # re-emitted message updates rather than duplicating.
        counter["turns"] >> PARENT["turns"]
        counter["notice"] >> PARENT["messages"]
        ended["stopped_early"] >> PARENT["stopped_early"]
        ended["truncated"] >> PARENT["truncated"]
        ended["finish_reason"] >> PARENT["finish_reason"]
        assistant["messages"] >> PARENT["messages"]
        closed["messages"] >> PARENT["messages"]
        gathered["messages"] >> PARENT["messages"]

        START >> counter >> asked >> context >> model >> assistant >> router
        # `closed` runs after `assistant`, so its answers land after the
        # message holding the calls they answer.
        router >> closed >> ended
        answer = answer_of(messages=PARENT["messages"])
        ended >> if_(router["finished"] == True, answer).else_(tools)  # noqa: E712
        answer >> END
        tools >> gathered >> counter  # back-edge — rewritten into a loop

    def agent(*args: Any, **kwargs: Any):
        """The agent node. Its answer, `final`, is what a viewer shows for it."""
        node = react(*args, **kwargs)
        node.show_keys = ("final",)
        return node

    register_skip(agent)  # the node is named after the caller's variable, not `node`
    return agent


def _takes_last_turn(call_model: Callable) -> bool:  # noqa: D401
    """Whether ``call_model`` declares a ``last_turn`` parameter.

    Named explicitly, not caught by ``**kwargs``: a factory that forwards
    its keywords to ``LLMOp.of`` would otherwise hand the flag to the
    model as a template variable.
    """
    try:
        parameters = inspect.signature(call_model).parameters
    except (TypeError, ValueError):
        return False
    param = parameters.get("last_turn")
    return param is not None and param.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    )


def final_of(messages: Any) -> Optional[dict]:
    """The agent's answer in a conversation: its last assistant message.

    A turn that asked for tools is not an answer, even when it is the last
    one: the budget can end the loop on it. Reporting it as the answer
    handed callers an empty "answer" and told them the run succeeded.
    """
    if not isinstance(messages, list):
        return None
    last = next(
        (m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "assistant"),
        None,
    )
    return None if last is not None and last.get("tool_calls") else last


@op
def answer_of(messages: Optional[list] = None) -> dict:
    """The agent graph's output: the answer, for whoever comes next.

    Without it an agent used as a node had nothing for the next op to read
    but its cells — the answer was reachable only through ``agent_result``
    after the whole run had finished.
    """
    return {"final": final_of(messages)}


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
    "truncated": False,
    "finish_reason": "",
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

    ``final`` is the last assistant message, or ``None`` when that message
    asked for tools — the loop ended (budget spent) before the model
    answered. ``stopped_early`` is True when the run ended before the
    model finished: the turn budget ran out, or the last response was cut
    off. ``truncated`` says which — the last response stopped at
    ``length``/``max_tokens``/``content_filter`` — and ``finish_reason``
    is that response's stop reason (``""`` if ``call_model`` reports none).

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
        "truncated": bool(cell("truncated", False)),
        "finish_reason": cell("finish_reason", "") or "",
        "final": final_of(messages),
    }
