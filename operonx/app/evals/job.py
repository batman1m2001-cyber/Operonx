"""`Eval` — a dataset, the system under test, evaluators — run as a Job."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from ..jobs import Job
from ..jobs.record import ITEM_EMPTY, ITEM_OK, RUN_FAILED, RUN_OK
from .dataset import Dataset, dataset_path
from .evaluators import _judge_one, _name

__all__ = ["Eval"]

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
        from ..serve.registry import load_object

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
