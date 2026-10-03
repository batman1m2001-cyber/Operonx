"""`compare` — any two experiments, paired, the way the gate compares them.

The gate compares a run with its baseline as the run finishes; this is the
same comparison (:func:`~operonx.app.evals.gate.compare_runs`) for any two
experiments after the fact — from records or the score store::

    a = load_experiment("20261004T101500-000001", store=store)
    b = load_experiment("20261004T113000-000002", store=store)
    got = compare(a, b, tolerance=0.03)
    got["verdict"], got["comparison"]["tests"][0]["diff"]

Without a ``tolerance`` every metric is reported — difference, interval,
p-value — and nothing is judged: a verdict needs someone to have said
how large a drop matters (D15).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from .experiments import ExperimentData
from .gate import (
    INCONCLUSIVE,
    PASS,
    Gate,
    PerMetric,
    _worst,
    comparability,
    compare_runs,
    exit_code,
)

__all__ = ["compare"]


def compare(
    a: ExperimentData,
    b: ExperimentData,
    *,
    tolerance: PerMetric = None,
    metrics: Optional[Sequence[str]] = None,
    alpha: float = 0.05,
    strict: bool = False,
    bootstrap: int = 2000,
) -> Dict[str, Any]:
    """*b* against *a* (the baseline), on the cases both hold unchanged.

    Returns ``{"baseline", "candidate", "verdict", "exit_code",
    "reasons", "warnings", "comparison"}``; ``verdict`` and ``exit_code``
    are ``None`` without a *tolerance*. *metrics* are the gated ones
    (default ``["pass"]``; Holm-adjusted), the rest exploratory (BH).
    """
    gate = Gate(
        tolerance=tolerance,
        metrics=list(metrics) if metrics else None,
        alpha=alpha,
        strict=strict,
        bootstrap=bootstrap,
        must_pass_tag=None,
    )
    comparison = compare_runs(gate, a.outcomes(), b.outcomes(), baseline_id=a.experiment_id)
    comparison["baseline"] = a.experiment_id
    warnings = comparability(a.experiment_id, a.fingerprint, b.fingerprint, comparison["cases"])
    if a.eval != b.eval:
        warnings.insert(0, f"two different evals: {a.eval!r} and {b.eval!r}")
    judged = [t for t in comparison["tests"] if t.get("verdict")]
    reasons = [
        f"{t['metric']}: {t['diff'] * 100:+.1f} pts [{t['ci_lo'] * 100:+.1f}, "
        f"{t['ci_hi'] * 100:+.1f}], p={t['p_holm']:.3g} ({t['method']}, Holm), tolerance "
        f"{t['tolerance'] * 100:.1f} pts → {t['verdict']}"
        for t in judged
        if t["verdict"] != PASS
    ]
    found = [t["verdict"] for t in judged]
    for m in gate.gated():
        if m in comparison["not_compared"]:
            found.append(INCONCLUSIVE)
            reasons.append(f"{m}: no shared case measured it in both experiments")
    verdict = _worst(found) if tolerance is not None else None
    return {
        "baseline": a.experiment_id,
        "candidate": b.experiment_id,
        "eval": b.eval,
        "verdict": verdict,
        "exit_code": exit_code(verdict, strict) if verdict is not None else None,
        "reasons": reasons,
        "warnings": warnings,
        "alpha": alpha,
        "comparison": comparison,
    }
