"""Evaluators: what judges a case, and how any result becomes a verdict.

An evaluator is a function — plain, async, or an ``@op`` (called for its
body) — that takes any of ``input``, ``output``, ``expected``, ``row`` and
``outputs`` by name and returns ``True``/``False``, a score in [0, 1]
(passes at 0.5), or ``{"passed", "score", "reason"}``.
"""

from __future__ import annotations

import difflib
import inspect
import json
from collections.abc import Mapping
from time import perf_counter
from typing import Any, Callable, Dict, Optional, Sequence

__all__ = ["contains", "exact", "fuzzy", "json_match", "llm_judge", "verdict_of"]

# ── verdicts ─────────────────────────────────────────────────────────────


def verdict_of(value: Any) -> Dict[str, Any]:
    """Any evaluator result as ``{"passed", "score"?, "reason"?, …}``."""
    if isinstance(value, bool):
        return {"passed": value}
    if isinstance(value, (int, float)):
        return {"passed": float(value) >= 0.5, "score": float(value)}
    if isinstance(value, Mapping):
        out = {k: v for k, v in value.items() if v is not None}
        if "passed" not in out:
            score = out.get("score")
            out["passed"] = bool(score is not None and float(score) >= 0.5)
        out["passed"] = bool(out["passed"])
        return out
    if value is None:
        return {"passed": False, "reason": "the evaluator returned nothing"}
    return {"passed": bool(value)}


def _name(ev: Any) -> str:
    return str(getattr(ev, "eval_name", None) or getattr(ev, "__name__", None) or type(ev).__name__)


async def _judge_one(ev: Any, avail: Dict[str, Any]) -> Dict[str, Any]:
    fn = getattr(ev, "__wrapped__", ev)  # an @op is called for its body
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kwargs = dict(avail)
    else:
        kwargs = {k: v for k, v in avail.items() if k in params}
    t0 = perf_counter()
    try:
        result = fn(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        verdict = verdict_of(result)
    except Exception as exc:  # noqa: BLE001 — a broken evaluator fails its case, loudly
        verdict = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
    verdict["ms"] = round((perf_counter() - t0) * 1000, 3)
    return verdict


# ── built-in evaluators ──────────────────────────────────────────────────


def _pick(value: Any, field: Optional[str]) -> Any:
    if field is None:
        return value
    for part in field.split("."):
        value = value.get(part) if isinstance(value, Mapping) else None
    return value


def exact(field: Optional[str] = None) -> Callable:
    """Passes when the output (or its ``field``, dotted) equals ``expected``."""

    def exact_match(output: Any = None, expected: Any = None) -> Dict[str, Any]:
        got = _pick(output, field)
        want = _pick(expected, field) if isinstance(expected, Mapping) and field else expected
        ok = got == want
        return {"passed": ok, "reason": None if ok else f"got {got!r}, expected {want!r}"}

    exact_match.eval_name = f"exact({field})" if field else "exact"
    return exact_match


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return " ".join(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def contains(*needles: str, field: Optional[str] = None, case: bool = False) -> Callable:
    """Passes when the output text contains every needle — the given ones,
    else ``expected`` (a string or a list of strings)."""

    def contains_all(output: Any = None, expected: Any = None) -> Dict[str, Any]:
        text = _text(_pick(output, field))
        want = list(needles) or ([expected] if isinstance(expected, str) else list(expected or []))
        hay = text if case else text.lower()
        missing = [n for n in want if (n if case else str(n).lower()) not in hay]
        return {
            "passed": not missing and bool(want),
            "reason": f"missing {missing}"
            if missing
            else (None if want else "nothing to look for"),
        }

    contains_all.eval_name = "contains"
    return contains_all


def fuzzy(threshold: float = 0.8, field: Optional[str] = None) -> Callable:
    """Similarity of the output text to ``expected`` (0–1); passes at *threshold*."""

    def fuzzy_match(output: Any = None, expected: Any = None) -> Dict[str, Any]:
        a, b = _text(_pick(output, field)), _text(expected)
        score = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
        return {"passed": score >= threshold, "score": round(score, 4)}

    fuzzy_match.eval_name = f"fuzzy({threshold:g})"
    return fuzzy_match


def json_match(keys: Optional[Sequence[str]] = None) -> Callable:
    """Passes when the output object agrees with ``expected`` on *keys*
    (every key ``expected`` has, when none are named); score is the share
    that agree."""

    def json_agree(output: Any = None, expected: Any = None) -> Dict[str, Any]:
        if not isinstance(output, Mapping) or not isinstance(expected, Mapping):
            return {"passed": False, "reason": "output and expected must both be objects"}
        names = list(keys or expected.keys())
        wrong = [k for k in names if _pick(output, k) != _pick(expected, k)]
        score = 1 - len(wrong) / len(names) if names else 1.0
        return {
            "passed": not wrong,
            "score": round(score, 4),
            "reason": f"differs on {wrong}" if wrong else None,
        }

    json_agree.eval_name = "json_match"
    return json_agree


def llm_judge(resource: str, rubric: str, *, name: str = "llm_judge") -> Callable:
    """An LLM grades the case against *rubric*: ``{passed, score, reason}``
    parsed from its JSON answer, with the call's cost and usage kept on
    the verdict. *resource* is an ``llm:`` key (or a bare name) the hub
    resolves."""
    safe_rubric = rubric.replace("{", "{{").replace("}", "}}")

    async def judge(input: Any = None, output: Any = None, expected: Any = None) -> Dict[str, Any]:  # noqa: A002
        from operonx.core import END, START, Operon
        from operonx.core.ops.graph.graph_op import GraphOp
        from operonx.providers.ops import LLMOp

        def show(v: Any) -> str:
            return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)

        with GraphOp(name="llm_judge") as g:
            node = LLMOp.of(
                resource=resource.partition(":")[2] if resource.startswith("llm:") else resource,
                prompt={
                    "system": safe_rubric + "\n\nAnswer with JSON only: "
                    '{{"passed": true|false, "score": 0.0-1.0, "reason": "one sentence"}}',
                    "user": "Input:\n{case_input}\n\nOutput:\n{case_output}\n\nExpected:\n{case_expected}",
                },
                fields=["passed: bool", "score: float", "reason: str"],
                parser="json",
                case_input=show(input),
                case_output=show(output),
                case_expected=show(expected) if expected is not None else "(none given)",
            )
            START >> node >> END
        out = await Operon(g).run(inputs={})
        if out.get("error"):
            return {"passed": False, "error": str(out["error"])}
        verdict = {
            "passed": bool(out.get("passed")),
            "score": out.get("score"),
            "reason": out.get("reason"),
        }
        if out.get("cost_usd") is not None or "usage" in out:
            verdict["cost_usd"] = out.get("cost_usd")
            verdict["usage"] = out.get("usage")
        return verdict

    judge.eval_name = name
    return judge
