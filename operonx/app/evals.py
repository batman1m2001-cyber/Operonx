"""Evals: a dataset of cases, the system under test, and evaluators.

An eval is a :class:`~operonx.app.jobs.Job` — no new runtime. Its source is
a dataset, its graph is the system under test (any graph a job can run,
doors or not), and after each case's run the evaluators judge what came
out. The item record carries the verdict, the runs carry ``origin=eval``
(filed under ``.operonx/runs/evals/``), and ``run.json`` carries the pass
rate. ``operonx run <eval>`` exits non-zero when a case fails — or, with a
``threshold``, when the pass rate is under it — so CI can gate on it::

    ev = Eval("replies", graph="bot:reply_flow", dataset="datasets/replies.jsonl",
              evaluators=[contains(), llm_judge("llm:judge", "Is the reply polite and correct?")],
              threshold=0.9)
    run = await ev.run()          # JobRun; run.meta["eval"] has the numbers

**A dataset** is a JSONL file, one case per line::

    {"id": "c1", "input": {...}, "expected": ..., "tags": ["refund"], "from": {"run": "…"}}

``input`` is the item the graph receives; ``expected`` is optional (an
evaluator that needs it says so). A line without ``input`` is itself the
input, so a job's data file is already a dataset of cases with no
expectations. ``"dataset:name"`` names ``<project>/datasets/name.jsonl``.

**An evaluator** is a function — plain, async, or an ``@op`` (called for
its body) — that takes any of ``input``, ``output``, ``expected``, ``row``
and ``outputs`` by name and returns a verdict: ``True``/``False``, a score
in [0, 1] (passes at 0.5), or ``{"passed", "score", "reason"}``. Helpers:
:func:`exact`, :func:`contains`, :func:`fuzzy`, :func:`json_match`,
:func:`llm_judge`. ``output`` is what the case produced: the one item the
graph sent (or its result, for a graph with no doors), a list when it sent
several, ``None`` when it sent nothing.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Union

from .jobs import Job
from .jobs.record import ITEM_EMPTY, ITEM_OK, RUN_FAILED, RUN_OK

__all__ = [
    "Dataset",
    "Eval",
    "contains",
    "dataset_path",
    "exact",
    "fuzzy",
    "json_match",
    "llm_judge",
    "verdict_of",
]

#: Row keys that describe a case rather than being its input.
CASE_KEYS = ("id", "input", "expected", "tags", "from", "note")


# ── datasets ─────────────────────────────────────────────────────────────


def dataset_path(ref: Union[str, Path], root: Union[str, Path, None] = None) -> Path:
    """``dataset:name`` → ``<root>/datasets/name.jsonl``; a path stays a path
    (relative to *root*)."""
    root = Path(root) if root is not None else Path.cwd()
    text = str(ref)
    if text.startswith("dataset:"):
        return root / "datasets" / f"{text.partition(':')[2]}.jsonl"
    path = Path(text)
    return path if path.is_absolute() else root / path


def case_id(row: Mapping) -> str:
    """A row's id: its own, else a hash of its input (stable across adds)."""
    if row.get("id") not in (None, ""):
        return str(row["id"])
    body = json.dumps(row.get("input", row), sort_keys=True, default=str)
    return hashlib.sha1(body.encode()).hexdigest()[:12]


class Dataset:
    """A JSONL file of cases. Reading is lazy; adding appends and dedupes."""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)

    @property
    def name(self) -> str:
        return self.path.stem

    def rows(self) -> List[Dict[str, Any]]:
        if not self.path.is_file():
            return []
        out: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    raise ValueError(f"{self.path}:{n}: not JSON ({exc})") from None
                if not isinstance(row, dict) or "input" not in row:
                    row = {"input": row}
                row["id"] = case_id(row)
                out.append(row)
        return out

    def add(self, rows: Iterable[Mapping]) -> List[str]:
        """Append *rows* whose id is not already there; returns the ids added."""
        have = {r["id"] for r in self.rows()}
        added: List[str] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for row in rows:
                row = dict(row)
                if "input" not in row:
                    raise ValueError("a case needs an `input`")
                row["id"] = case_id(row)
                if row["id"] in have:
                    continue
                have.add(row["id"])
                ordered = {k: row[k] for k in CASE_KEYS if k in row}
                ordered.update({k: v for k, v in row.items() if k not in ordered})
                fh.write(json.dumps(ordered, ensure_ascii=False, default=str) + "\n")
                added.append(row["id"])
        return added

    def __len__(self) -> int:
        return len(self.rows())

    def __repr__(self) -> str:
        return f"Dataset({str(self.path)!r})"


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


# ── the eval ─────────────────────────────────────────────────────────────


class _Capture:
    """The eval's sink: what each case sent, kept for its evaluators."""

    def __init__(self) -> None:
        self.by_key: Dict[str, List[Any]] = {}

    async def write(self, key: str, item: Any) -> None:
        self.by_key.setdefault(key, []).append(item)

    async def close(self) -> None:
        pass

    def __repr__(self) -> str:
        return "<eval capture>"


class Eval(Job):
    """A dataset, a graph, evaluators — run as a job with ``origin=eval``.

    Everything but the three below is a :class:`Job` argument
    (``concurrency``, ``item_timeout``, ``inputs``, ``item_input``,
    ``trace``…).

    Args:
        dataset: A :class:`Dataset`, a JSONL path, or ``"dataset:name"``.
        evaluators: Functions judging each case (see the module docstring).
        threshold: With it, the run fails when the pass rate is under it;
            without, it fails when any case fails.
    """

    origin = "eval"

    def __init__(
        self,
        name: str,
        *,
        graph: Any,
        dataset: Any,
        evaluators: Sequence[Any] = (),
        threshold: Optional[float] = None,
        record_dir: Union[str, Path] = "evals",
        **kwargs: Any,
    ):
        self.dataset = dataset if isinstance(dataset, Dataset) else Dataset(dataset_path(dataset))
        self.evaluators = list(evaluators)
        if threshold is not None and not 0 <= float(threshold) <= 1:
            raise ValueError(f"eval {name!r}: threshold is a pass rate in [0, 1]")
        self.threshold = float(threshold) if threshold is not None else None
        self._capture = _Capture()
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._verdicts: List[Dict[str, Any]] = []
        kwargs.setdefault("on_error", "record")
        super().__init__(
            name,
            graph=graph,
            source=self._cases,
            sink=self._capture,
            key="id",
            session="per_item",
            record_dir=record_dir,
            **kwargs,
        )

    async def _cases(self):
        for row in await asyncio.to_thread(self.dataset.rows):
            self._rows[row["id"]] = row
            yield row

    def item_of(self, raw: Any) -> Any:
        return raw["input"] if isinstance(raw, Mapping) and "input" in raw else raw

    async def judge(self, raw: Any, result: Any) -> None:
        """After a case's run: its evaluators, and the verdict on its record."""
        row = self._rows.get(result.key) or (raw if isinstance(raw, Mapping) else {"input": raw})
        sent = self._capture.by_key.pop(result.key, [])
        output = sent[0] if len(sent) == 1 else (sent or None)
        if result.status not in (ITEM_OK, ITEM_EMPTY):
            verdict = {"passed": False, "error": result.error or result.status, "checks": {}}
        else:
            avail = {
                "input": row.get("input"),
                "output": output,
                "expected": row.get("expected"),
                "row": row,
                "outputs": sent,
            }
            checks = {}
            for ev in self.evaluators:
                checks[_name(ev)] = await _judge_one(ev, avail)
            verdict = {
                "passed": all(c["passed"] for c in checks.values()) if checks else True,
                "checks": checks,
            }
            cost = [c["cost_usd"] for c in checks.values() if c.get("cost_usd") is not None]
            if cost:
                verdict["judge_cost_usd"] = round(sum(cost), 8)
        verdict["output"] = _clip(output)
        if row.get("expected") is not None:
            verdict["expected"] = _clip(row.get("expected"))
        if row.get("tags"):
            verdict["tags"] = list(row["tags"])
        result.verdict = verdict
        self._verdicts.append(
            {
                "key": result.key,
                "passed": verdict["passed"],
                "checks": {k: v["passed"] for k, v in verdict["checks"].items()},
                "ms": result.ms,
                "error": bool(verdict.get("error")),
                "judge_cost_usd": verdict.get("judge_cost_usd"),
            }
        )

    def summarize(self, status: str) -> tuple:
        """What run.json says about the eval, and the run's status: failed
        when a case failed, or with a threshold, when the rate is under it."""
        vs, self._verdicts = self._verdicts, []
        cases = len(vs)
        passed = sum(1 for v in vs if v["passed"])
        per_check: Dict[str, Dict[str, int]] = {}
        for v in vs:
            for k, ok in v["checks"].items():
                c = per_check.setdefault(k, {"passed": 0, "cases": 0})
                c["cases"] += 1
                c["passed"] += int(ok)
        ms = sorted(v["ms"] for v in vs if v["ms"])
        judge = [v["judge_cost_usd"] for v in vs if v.get("judge_cost_usd") is not None]
        summary = {
            "dataset": str(self.dataset.path),
            "cases": cases,
            "passed": passed,
            "failed": cases - passed,
            "errored": sum(1 for v in vs if v["error"]),
            "pass_rate": round(passed / cases, 4) if cases else None,
            "threshold": self.threshold,
            "checks": per_check,
            "p50_ms": ms[len(ms) // 2] if ms else None,
            "judge_cost_usd": round(sum(judge), 8) if judge else None,
        }
        if status == RUN_OK and cases:
            under = (
                (summary["pass_rate"] < self.threshold)
                if self.threshold is not None
                else passed < cases
            )
            if under:
                status = RUN_FAILED
        return {"eval": summary}, status

    @classmethod
    def from_spec(cls, spec: Any, root: Union[str, Path, None] = None) -> "Eval":
        """An Eval from a ``[[job]]`` block with ``dataset`` and ``evaluators``."""
        from .serve.registry import load_object

        root = Path(root) if root is not None else Path.cwd()
        opts = dict(spec.options)
        evaluators = [
            load_object(e, field=f"[[job]] {spec.name!r} evaluators") if isinstance(e, str) else e
            for e in opts.get("evaluators") or []
        ]
        record_dir = Path(spec.record_dir) if spec.record_dir else Path("evals")
        return cls(
            spec.name,
            graph=spec.graph,
            dataset=dataset_path(opts["dataset"], root),
            evaluators=evaluators,
            threshold=opts.get("threshold"),
            record_dir=record_dir if record_dir.is_absolute() else root / record_dir,
            concurrency=spec.concurrency,
            item_timeout=spec.item_timeout,
            trace=list(spec.trace) if spec.trace is not None else None,
            inputs=dict(spec.inputs),
            item_input=spec.item_input,
            schedule=spec.schedule,
            description=spec.description,
        )

    def describe(self) -> Dict[str, Any]:
        out = super().describe()
        # the cases come from the dataset and the outputs go to the verdicts:
        # the source and sink underneath are plumbing, not what the eval is
        out.update(
            {
                "kind": "eval",
                "source": str(self.dataset.path),
                "sink": None,
                "dataset": str(self.dataset.path),
                "evaluators": [_name(e) for e in self.evaluators],
                "threshold": self.threshold,
            }
        )
        return out


def _clip(value: Any, limit: int = 4000) -> Any:
    """A value small enough for a record line; large ones as a preview."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)[:limit]
    return value if len(text) <= limit else text[:limit] + "…"
