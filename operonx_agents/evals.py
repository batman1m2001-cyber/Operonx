"""Trajectory evaluators for agent runs, and ``dataset_from_runs``.

An agent eval is an operonx ``Eval`` (a Job: no new runtime). Its system
under test is a graph running the agent — ``agent_service``'s, or one with
``agent.as_op()`` — and these evaluators read how the run got its answer
from the case's trace (a :class:`~operonx.app.evals.TraceView`: the agent
op, each ``turn``, each ``model`` call and each tool call, as the runner
records them)::

    from operonx.app.evals import Eval, Gate
    from operonx_agents.evals import (
        cost_at_most, no_tool_errors, output_valid, tool_called, tool_not_called, turns_at_most,
    )

    ev = Eval("support", graph=support_service.graph, dataset="dataset:support",
              evaluators=[tool_called("order_status", args={"order_id": "A1"}),
                          tool_not_called("refund"), no_tool_errors(), turns_at_most(4),
                          output_valid(), cost_at_most(0.01)],
              gate=Gate(threshold=0.9))

Each takes ``agent=`` to look at one agent's steps only (a sub-agent run
through ``as_tool`` records its own turns, under the parent's tool call);
without it, every agent in the run counts.

``output`` is read the way an agent graph sends it: ``agent_service``'s
reply (``{"status", "output", ...}``), the events of a streamed run (the
``RunFinished`` result), or ``as_op()``'s outputs.

:func:`dataset_from_runs` turns recorded runs into eval cases: what each
agent was asked as ``input``, the tool calls it made as the reference
``trajectory``, its answer as ``expected``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, Dict, Iterable, List, Optional

from operonx.app.evals import TraceView, budget
from operonx.core.workflow_trace import child_parent_id
from pydantic import TypeAdapter, ValidationError

__all__ = [
    "cost_at_most",
    "dataset_from_runs",
    "no_tool_errors",
    "output_valid",
    "result_of",
    "tool_called",
    "tool_not_called",
    "turns_at_most",
]

#: The op types the runner records (operonx ``OpType`` and its children's).
AGENT, TURN, MODEL, TOOL = "agent", "turn", "llm", "tool"


def _named(fn: Callable, name: str) -> Callable:
    fn.eval_name = name
    return fn


def _need(trace: Any, who: str) -> TraceView:
    if trace is None:
        raise ValueError(
            f"{who} reads the case's trace, and this case has none "
            "(the item did not run, or the evaluator was called outside an eval)"
        )
    return trace


def _agent_of(view: TraceView, row: Any) -> Optional[str]:
    """The agent a model or tool step belongs to: its turn's
    ``gen_ai.agent.name`` (the runner sets it on every turn)."""
    by_id = {r.op_id: r for r in view.rows}
    seen = row
    while seen is not None:
        if seen.op_type == TURN:
            return seen.attrs.get("gen_ai.agent.name")
        parent = child_parent_id(seen.op_full_name, seen.ctx)
        seen = by_id.get(parent) if parent else None
    return None


def _rows(view: TraceView, op_type: str, agent: Optional[str]) -> List[Any]:
    rows = view.ops(type=op_type)
    return rows if agent is None else [r for r in rows if _agent_of(view, r) == agent]


def _calls(view: TraceView, agent: Optional[str]) -> List[Any]:
    calls = view.tool_calls()
    if agent is None:
        return calls
    models = {r.op_id: r for r in view.rows}
    return [c for c in calls if _agent_of(view, models[c.op_id]) == agent]


def _args_match(actual: Any, wanted: Optional[Mapping[str, Any]]) -> bool:
    if wanted is None:
        return True
    return isinstance(actual, Mapping) and all(
        k in actual and actual[k] == v for k, v in wanted.items()
    )


def _who(agent: Optional[str]) -> str:
    return f", agent={agent!r}" if agent else ""


def tool_called(
    name: str,
    args: Optional[Mapping[str, Any]] = None,
    *,
    times: Optional[int] = None,
    agent: Optional[str] = None,
) -> Callable:
    """The model called ``name`` — with at least ``args`` (each given
    argument equal; the call may have more), exactly ``times`` times when
    given, else at least once."""

    def evaluate(trace: Any = None) -> Dict[str, Any]:
        calls = _calls(_need(trace, "tool_called"), agent)
        same = [c for c in calls if c.name == name and _args_match(c.args, args)]
        ok = len(same) == times if times is not None else bool(same)
        if ok:
            return {"passed": True}
        made = [{"name": c.name, "args": c.args} for c in calls] or "no tool calls"
        want = f"{name}({args})" if args else name
        count = f" {times} time(s)" if times is not None else ""
        return {
            "passed": False,
            "reason": f"expected {want}{count}, matched {len(same)}; the calls were {made}",
        }

    label = f"{name}, args={dict(args)}" if args else name
    count = f", times={times}" if times is not None else ""
    return _named(evaluate, f"tool_called({label}{count}{_who(agent)})")


def tool_not_called(name: str, *, agent: Optional[str] = None) -> Callable:
    """The model never called ``name`` (a refused or unknown call counts:
    the model asked for it)."""

    def evaluate(trace: Any = None) -> Dict[str, Any]:
        calls = [c for c in _calls(_need(trace, "tool_not_called"), agent) if c.name == name]
        if not calls:
            return {"passed": True}
        return {
            "passed": False,
            "reason": f"{name} was called {len(calls)} time(s): {[c.args for c in calls]}",
        }

    return _named(evaluate, f"tool_not_called({name}{_who(agent)})")


def no_tool_errors(*, agent: Optional[str] = None) -> Callable:
    """Every tool call was answered without an error: no bad arguments,
    unknown tool, refusal, exception or timeout reached the model."""

    def evaluate(trace: Any = None) -> Dict[str, Any]:
        bad = []
        for row in _rows(_need(trace, "no_tool_errors"), TOOL, agent):
            message = row.outputs.get("tool_message") if isinstance(row.outputs, Mapping) else None
            if row.status != "ok":
                bad.append(f"{row.op_name}: {row.status}")
            elif isinstance(message, Mapping) and message.get("status") == "error":
                bad.append(
                    f"{message.get('name') or row.op_name}: {str(message.get('content'))[:120]}"
                )
        return {"passed": not bad, "reason": "; ".join(bad) or None}

    return _named(evaluate, f"no_tool_errors({_who(agent).lstrip(', ')})")


def turns_at_most(n: int, *, agent: Optional[str] = None) -> Callable:
    """The run took at most ``n`` model turns (every agent's, or
    ``agent``'s)."""
    if n < 1:
        raise ValueError(f"turns_at_most({n}): an agent answers in one turn at least")

    def evaluate(trace: Any = None) -> Dict[str, Any]:
        turns = len(_rows(_need(trace, "turns_at_most"), TURN, agent))
        return {
            "passed": turns <= n,
            "score": 1.0 if turns <= n else round(n / turns, 4),
            "reason": None if turns <= n else f"{turns} turns > {n}",
            "measured": {"turns": turns},
        }

    return _named(evaluate, f"turns_at_most({n}{_who(agent)})")


def result_of(output: Any) -> Optional[Dict[str, Any]]:
    """The run's result in what an agent graph sent: a reply dict, the
    events of a streamed run (the last ``RunFinished``'s ``result``), a
    ``RunResult``, or ``as_op()``'s outputs. ``None`` when there is none."""
    if hasattr(output, "to_dict") and hasattr(output, "status"):
        return output.to_dict()
    if isinstance(output, Mapping):
        if output.get("type") == "RunFinished":
            return dict(output.get("result") or {})
        return dict(output) if "status" in output else None
    if isinstance(output, (list, tuple)):
        for item in reversed(output):
            found = result_of(item)
            if found is not None:
                return found
    return None


def output_valid(type_: Any = None) -> Callable:
    """The run completed, and — with ``type_`` (a pydantic model, or any
    type pydantic validates) — its output validates against it."""
    adapter = TypeAdapter(type_) if type_ is not None else None

    def evaluate(output: Any = None) -> Dict[str, Any]:
        result = result_of(output)
        if result is None:
            return {"passed": False, "reason": f"no agent result in the output: {output!r:.200}"}
        if result.get("status") != "completed":
            why = result.get("error") or result.get("limit_hit") or "no answer"
            return {"passed": False, "reason": f"the run ended {result.get('status')}: {why}"}
        if adapter is not None:
            try:
                adapter.validate_python(result.get("output"))
            except ValidationError as exc:
                return {"passed": False, "reason": f"the output does not validate: {exc}"}
        return {"passed": True}

    shown = getattr(type_, "__name__", repr(type_)) if type_ is not None else ""
    return _named(evaluate, f"output_valid({shown})")


def cost_at_most(usd: float) -> Callable:
    """The run cost at most ``usd`` (every priced model call, sub-agents'
    included). A run with an unpriced call fails: its cost is unknown."""
    return budget(cost_usd=usd, name=f"cost_at_most({usd:g})")


# ── dataset_from_runs ────────────────────────────────────────────────────


def dataset_from_runs(
    store: Any,
    where: Any = None,
    *,
    agent: Optional[str] = None,
    limit: int = 100,
    expected: bool = True,
) -> List[Dict[str, Any]]:
    """Recorded runs as eval cases, one per agent run in them.

    Args:
        store: A run store (``operonx.telemetry.runs``: files, SQLite, ...).
        where: A ``RunFilter`` (the service, a time window, ``status``).
        agent: Only this agent's runs (the agent op's name).
        limit: At most this many runs are read.
        expected: Keep the agent's last answer as ``expected``
            (``{"output": ...}``), for a check that compares answers.

    Returns:
        Cases ``{"id", "input", "trajectory": {"tool_calls": [...]},
        "expected"?, "tags", "from": {"run", "op"}}``: ``input`` is what
        the agent was asked (its first turn's record: a user message as its
        text, else the messages), which ``agent_service``'s graph and
        ``as_op()`` both take — without a session's history, which the
        trace does not hold. Values are as the store holds them: redacted
        when the agent redacts. A resumed run is not a case; a run with no
        agent op adds none. Add them to a dataset with
        ``Dataset(path).add(cases)``.
    """
    cases: List[Dict[str, Any]] = []
    for summary in _runs(store, where, limit):
        view = TraceView.from_store(store, summary.trace_id)
        for row in view.ops(type=AGENT):
            if agent is not None and row.op_name != agent:
                continue
            steps = _under(view, row)
            asked = _asked(view, row, steps)
            if asked is None:
                continue
            case: Dict[str, Any] = {
                "id": f"{summary.trace_id}:{row.op_name}:{len(cases)}",
                "input": asked,
                "trajectory": {
                    "tool_calls": [
                        {"name": c.name, "args": c.args}
                        for c in view.tool_calls()
                        if c.op_id in steps
                    ]
                },
                "tags": ["from_runs", row.op_name],
                "from": {"run": summary.trace_id, "op": row.op_id},
            }
            answer = _answer(view, steps)
            if expected and answer is not None:
                case["expected"] = {"output": answer}
            cases.append(case)
    return cases


def _runs(store: Any, where: Any, limit: int) -> List[Any]:
    """Up to ``limit`` run summaries, oldest first, across pages."""
    out: List[Any] = []
    cursor = None
    while len(out) < limit:
        page = store.list_runs(where, order="started_asc", limit=limit - len(out), cursor=cursor)
        out.extend(page.items)
        cursor = page.next_cursor
        if not cursor or not page.items:
            break
    return out


def _asked(view: TraceView, root: Any, steps: set) -> Any:
    """What one agent run was asked: its first turn's ``input`` (the runner
    records it there), one user message as its text. ``None`` for a
    resumed run, which was asked nothing new."""
    for row in view.ops(type=TURN):
        if row.op_id not in steps or child_parent_id(row.op_full_name, row.ctx) != root.op_id:
            continue
        asked = row.inputs.get("input") if isinstance(row.inputs, Mapping) else None
        if not asked:
            return None
        if (
            len(asked) == 1
            and isinstance(asked[0], Mapping)
            and asked[0].get("role") == "user"
            and isinstance(asked[0].get("content"), str)
        ):
            return asked[0]["content"]
        return asked
    return None


def _under(view: TraceView, root: Any) -> set:
    """The op ids of every step recorded under ``root``, however deep."""
    by_parent: Dict[str, List[str]] = {}
    for r in view.rows:
        parent = child_parent_id(r.op_full_name, r.ctx)
        if parent:
            by_parent.setdefault(parent, []).append(r.op_id)
    out: set = set()
    todo = [root.op_id]
    while todo:
        for kid in by_parent.get(todo.pop(), ()):
            if kid not in out:
                out.add(kid)
                todo.append(kid)
    return out


def _answer(view: TraceView, steps: Iterable[str]) -> Any:
    """The last model reply under the agent op that called no tool."""
    steps = set(steps)
    replies = [
        r
        for r in view.ops(type=MODEL)
        if r.op_id in steps and isinstance(r.outputs, Mapping) and not r.outputs.get("tool_calls")
    ]
    return replies[-1].outputs.get("content") if replies else None
