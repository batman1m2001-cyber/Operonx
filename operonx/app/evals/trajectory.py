"""Evaluators over the run, not just its output: the path, the tool calls,
one op's output, and the budget.

Each takes the case's ``trace`` (a :class:`~.traceview.TraceView`)::

    evaluators=[
        trajectory.ops(["classify", "lookup_order", "reply"], mode="strict"),
        trajectory.tool_calls([{"name": "lookup", "args": {"order_id": "42"}}],
                              mode="superset", args="subset"),
        trajectory.op_output("classify", exact("kind")),
        budget(ms=1500, cost_usd=0.002, llm_calls=2),
    ]

**Modes** (AgentEvals'): ``strict`` — the same calls in the same order;
``unordered`` — the same calls in any order; ``subset`` — nothing beyond
the reference (every actual call matches a distinct reference call);
``superset`` — at least the reference (every reference call matches a
distinct actual call). Repeats count: a reference with one ``a`` is not a
superset of ``a, a``'s needs twice. Matching is a maximum matching, so a
loose reference entry is never used up by the call a stricter one needed.

**Arguments** of a tool call: ``exact`` — equal; ``subset`` — every
argument the reference gives is in the call with the same value (the call
may have more); ``ignore`` — names only. A reference entry may be a bare
name, or omit ``args``: it then matches on the name alone.

Without a ``reference``, the case's ``trajectory`` gives it
(``{"ops": [...], "tool_calls": [{"name", "args"}]}``); a case with
neither is an error on that case, never a silent pass. The score is the
share of the reference matched (``subset``: of the actual calls).
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .evaluators import _name, prepare, verdict_of

__all__ = ["budget", "op_output", "ops", "tool_calls"]

MODES = ("strict", "unordered", "subset", "superset")
ARGS = ("exact", "subset", "ignore")
_ANY = object()  # a reference entry with no args: any args match


def _check(value: str, allowed: Sequence[str], what: str) -> str:
    if value not in allowed:
        raise ValueError(f"{what} is {value!r}; one of {', '.join(allowed)}")
    return value


def _need_trace(trace: Any, who: str) -> Any:
    if trace is None:
        raise ValueError(
            f"{who} reads the case's trace, and this case has none "
            "(the item did not run, or the evaluator was called outside an eval)"
        )
    return trace


def _reference(given: Any, row: Any, key: str, who: str) -> List[Any]:
    if given is not None:
        return list(given)
    held = row.get("trajectory") if isinstance(row, Mapping) else None
    if isinstance(held, Mapping) and held.get(key) is not None:
        return list(held[key])
    raise ValueError(
        f"{who} has no reference: pass reference=[...] or give the case "
        f'"trajectory": {{"{key}": [...]}}'
    )


# ── matching ─────────────────────────────────────────────────────────────


def _matching(
    actual: Sequence[Any], reference: Sequence[Any], match: Callable[[Any, Any], bool]
) -> Dict[int, int]:
    """A maximum matching ``{reference index: actual index}`` (Kuhn's
    augmenting paths — trajectories are short)."""
    edges = [[j for j, a in enumerate(actual) if match(a, r)] for r in reference]
    owner: Dict[int, int] = {}  # actual index → reference index

    def augment(i: int, seen: set) -> bool:
        for j in edges[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in owner or augment(owner[j], seen):
                owner[j] = i
                return True
        return False

    for i in range(len(reference)):
        augment(i, set())
    return {i: j for j, i in owner.items()}


def _compare(
    actual: Sequence[Any],
    reference: Sequence[Any],
    mode: str,
    match: Callable[[Any, Any], bool],
    show: Callable[[Any], Any],
) -> Dict[str, Any]:
    if mode == "strict":
        ok = len(actual) == len(reference) and all(map(match, actual, reference))
        matched = sum(1 for a, r in zip(actual, reference) if match(a, r))
        score = matched / len(reference) if reference else float(ok)
        reason = (
            None
            if ok
            else f"expected {[show(r) for r in reference]}, got {[show(a) for a in actual]}"
        )
        return {"passed": ok, "score": round(score, 4), "reason": reason}
    pairs = _matching(actual, reference, match)
    missing = [show(r) for i, r in enumerate(reference) if i not in pairs]
    used = set(pairs.values())
    extra = [show(a) for j, a in enumerate(actual) if j not in used]
    if mode == "unordered":
        ok = not missing and not extra
    elif mode == "subset":
        ok = not extra
    else:  # superset
        ok = not missing
    base = len(actual) if mode == "subset" else len(reference)
    score = len(pairs) / base if base else float(ok)
    reasons = []
    if missing and mode != "subset":
        reasons.append(f"missing {missing}")
    if extra and mode != "superset":
        reasons.append(f"unexpected {extra}")
    return {
        "passed": ok,
        "score": round(score, 4),
        "reason": "; ".join(reasons) if not ok else None,
    }


# ── the evaluators ───────────────────────────────────────────────────────


def ops(
    reference: Optional[Sequence[str]] = None,
    *,
    mode: str = "strict",
    types: Optional[Sequence[str]] = None,
    collapse: bool = False,
    name: Optional[str] = None,
) -> Callable:
    """The op path (``trace.path(types=…, collapse=…)``) against *reference*."""
    _check(mode, MODES, "trajectory.ops mode")
    types = list(types) if types is not None else None

    def trajectory_ops(trace: Any = None, row: Any = None) -> Dict[str, Any]:
        path = _need_trace(trace, "trajectory.ops").path(types=types, collapse=collapse)
        ref = [str(r) for r in _reference(reference, row, "ops", "trajectory.ops")]
        return _compare(path, ref, mode, lambda a, r: a == r, lambda x: x)

    trajectory_ops.eval_name = name or f"trajectory.ops({mode})"
    return trajectory_ops


def _ref_call(entry: Any) -> Tuple[str, Any]:
    if isinstance(entry, str):
        return entry, _ANY
    if isinstance(entry, Mapping) and entry.get("name"):
        return str(entry["name"]), entry["args"] if "args" in entry else _ANY
    raise ValueError(f"a reference tool call is a name or {{'name', 'args'?}}, not {entry!r}")


def tool_calls(
    reference: Optional[Sequence[Any]] = None,
    *,
    mode: str = "strict",
    args: str = "exact",
    name: Optional[str] = None,
) -> Callable:
    """The tool calls the LLM calls made (``trace.tool_calls()``) against
    *reference*: names, and arguments as *args* says."""
    _check(mode, MODES, "trajectory.tool_calls mode")
    _check(args, ARGS, "trajectory.tool_calls args")

    def same(call: Any, ref: Tuple[str, Any]) -> bool:
        ref_name, ref_args = ref
        if call.name != ref_name:
            return False
        if args == "ignore" or ref_args is _ANY:
            return True
        if args == "exact":
            return call.args == ref_args
        return (
            isinstance(call.args, Mapping)
            and isinstance(ref_args, Mapping)
            and all(k in call.args and call.args[k] == v for k, v in ref_args.items())
        )

    def show(x: Any) -> Any:
        if isinstance(x, tuple):
            return x[0] if x[1] is _ANY or args == "ignore" else {"name": x[0], "args": x[1]}
        return x.name if args == "ignore" else {"name": x.name, "args": x.args}

    def trajectory_tool_calls(trace: Any = None, row: Any = None) -> Dict[str, Any]:
        calls = _need_trace(trace, "trajectory.tool_calls").tool_calls()
        ref = [
            _ref_call(e) for e in _reference(reference, row, "tool_calls", "trajectory.tool_calls")
        ]
        return _compare(calls, ref, mode, same, show)

    trajectory_tool_calls.eval_name = name or f"trajectory.tool_calls({mode}, args={args})"
    return trajectory_tool_calls


def op_output(op: str, check: Any, *, at: str = "last", name: Optional[str] = None) -> Callable:
    """*check* (any evaluator) on one op's outputs instead of the case's:
    ``output`` is that execution's outputs (``at="first"`` or ``"last"``),
    and the verdict's ``op`` is its ``op_id``. An op that never ran fails."""
    _check(at, ("first", "last"), "op_output at")
    inner = prepare(check)

    def op_output_check(
        trace: Any = None,
        input: Any = None,
        expected: Any = None,
        row: Any = None,  # noqa: A002
    ) -> Any:
        view = _need_trace(trace, "op_output")
        found = view.first(op) if at == "first" else view.last(op)
        if found is None:
            return {"passed": False, "reason": f"op {op!r} did not run"}
        avail = {
            "input": input,
            "output": found.outputs,
            "expected": expected,
            "row": row,
            "outputs": [found.outputs],
            "trace": view,
        }
        result = inner.fn(**inner.kwargs(avail))
        if inspect.isawaitable(result):

            async def settled() -> Dict[str, Any]:
                return {**verdict_of(await result), "op": found.op_id}

            return settled()
        return {**verdict_of(result), "op": found.op_id}

    op_output_check.eval_name = name or f"op_output({op}:{_name(check)})"
    return op_output_check


def budget(
    ms: Optional[float] = None,
    cost_usd: Optional[float] = None,
    tokens: Optional[int] = None,
    llm_calls: Optional[int] = None,
    *,
    name: str = "budget",
) -> Callable:
    """Limits on the case's run, each inclusive: ``ms`` (the graph run's
    duration), ``cost_usd``, ``tokens`` (in + out), ``llm_calls``. A cost
    limit over a run with unpriced calls fails: that cost is unknown."""
    limits = {"ms": ms, "cost_usd": cost_usd, "tokens": tokens, "llm_calls": llm_calls}
    limits = {k: v for k, v in limits.items() if v is not None}
    if not limits:
        raise ValueError("budget() needs at least one limit: ms, cost_usd, tokens or llm_calls")

    def within_budget(trace: Any = None) -> Dict[str, Any]:
        view = _need_trace(trace, "budget")
        measured = {
            "ms": round(view.duration_ms, 3),
            # unknown when any call went unpriced; nothing priced and no call costs 0
            "cost_usd": None if view.unpriced else (view.cost_usd or 0.0),
            "tokens": view.tokens,
            "llm_calls": len(view.llm_calls()),
        }
        over = []
        for key, limit in limits.items():
            got = measured[key]
            if got is None:
                over.append(f"cost unknown: {view.unpriced} unpriced call(s)")
            elif got > limit:
                over.append(f"{key} {got:g} > {limit:g}")
        return {
            "passed": not over,
            "reason": "; ".join(over) or None,
            "measured": {k: measured[k] for k in limits},
        }

    within_budget.eval_name = name
    return within_budget
