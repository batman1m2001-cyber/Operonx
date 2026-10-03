"""The noise floor and the power of an eval — evidence before a tolerance.

**calibrate.** Run the same code k times (an A/A test) and measure how
much the numbers move on their own. Each case passes with its own
probability p_i, estimated from its k·r trials; a case that passed every
trial is simulated as deterministic, so more runs see more of the rare
flakes. The tolerance the gate needs is then measured *through the gate*:
pairs of synthetic A/A runs drawn from those p_i are compared by
:func:`~operonx.app.evals.gate.compare_runs` — the same interval, McNemar
or bootstrap, clusters and all — and a gated metric passes exactly when
``ci_lo ≥ −tolerance``. The tolerance at which 95% of A/A comparisons
pass is the 95th percentile of ``−ci_lo``. It includes the case-sampling
uncertainty the interval carries, not just the flakes: 40 cases that
always pass still cannot rule out a 9-point drop. The table gives it for
r = 1, 2, 3, 5 repeats (and the configured r)::

    got = calibrate([exp1, exp2, exp3], target=0.03)
    got["rows"], got["recommended_repeats"], got["notes"]

**power.** :func:`~operonx.app.evals.stats.paired_sample_size` with the
discordance two experiments of the eval actually show
(:func:`discordance`).
"""

from __future__ import annotations

import math
import random
from statistics import stdev
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .experiments import ExperimentData
from .gate import PASS_METRIC, CaseOutcome, Gate, compare_runs
from .stats import seed_of

__all__ = ["DEFAULT_REPEATS", "calibrate", "discordance"]

#: The repeats ``calibrate`` tables a tolerance for (plus the eval's own).
DEFAULT_REPEATS = (1, 2, 3, 5)

#: The share of A/A comparisons that must pass for a tolerance to hold.
AA_PASS = 0.95


def _shared(experiments: Sequence[ExperimentData]) -> List[str]:
    """Case ids every experiment holds, with the same ``case_hash``."""
    sets = [e.outcomes() for e in experiments]
    ids = sorted(set.intersection(*(set(s) for s in sets)))
    return [c for c in ids if len({s[c].case_hash for s in sets if s[c].case_hash}) <= 1]


def _unbiased_var(c: int, m: int) -> float:
    """p(1 − p) estimated without bias from *c* passes of *m* trials."""
    return c * (m - c) / (m * (m - 1)) if m > 1 else 0.0


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def _ci_lo(gate: Gate, a: Dict[str, CaseOutcome], b: Dict[str, CaseOutcome], seed: str) -> float:
    cmp = compare_runs(gate, a, b, baseline_id=seed)
    test = next(t for t in cmp["tests"] if t["metric"] == PASS_METRIC)
    return float(test["ci_lo"])


def calibrate(
    experiments: Sequence[ExperimentData],
    *,
    target: Optional[float] = None,
    alpha: float = 0.05,
    repeats: Sequence[int] = DEFAULT_REPEATS,
    simulations: int = 200,
    bootstrap: int = 2000,
) -> Dict[str, Any]:
    """The A/A noise floor of *experiments* — runs of one eval on one
    commit — and the tolerance the gate needs at each number of repeats.

    *target* is the tolerance wanted (the gate's): the recommendation is
    the fewest repeats that reach it. See the module docstring.
    """
    if len(experiments) < 2:
        raise ValueError(
            "calibrate needs at least two runs of the same code (three is the usual: "
            "--runs 3) — one run shows no run-to-run noise"
        )
    evals = sorted({e.eval for e in experiments})
    if len(evals) > 1:
        raise ValueError(f"calibrate compares runs of one eval, not of {evals}")
    shas = sorted(
        {
            str(e.fingerprint["code_version"])
            for e in experiments
            if e.fingerprint.get("code_version")
        }
    )
    ids = _shared(experiments)
    if not ids:
        raise ValueError("the experiments share no unchanged case")
    sets = [e.outcomes() for e in experiments]
    cluster = {c: sets[0][c].cluster for c in ids}
    passes = {c: sum(sum(s[c].passed) for s in sets) for c in ids}
    trials = {c: sum(len(s[c].passed) for s in sets) for c in ids}
    p = {c: passes[c] / trials[c] for c in ids}
    var = {c: _unbiased_var(passes[c], trials[c]) for c in ids}
    configured = int(experiments[0].summary.get("repeats") or 1)
    n = len(ids)

    gate = Gate(alpha=alpha, bootstrap=bootstrap, must_pass_tag=None)
    rows: List[Dict[str, Any]] = []
    for r in sorted({*repeats, configured}):
        rng = random.Random(seed_of("calibrate", *sorted(e.experiment_id for e in experiments), r))

        def draw() -> Dict[str, CaseOutcome]:
            return {
                c: CaseOutcome(
                    c, cluster=cluster[c], passed=[rng.random() < p[c] for _ in range(r)]
                )
                for c in ids
            }

        lows = [_ci_lo(gate, draw(), draw(), f"aa{r}#{s}") for s in range(simulations)]
        row: Dict[str, Any] = {
            "repeats": r,
            # the smallest tolerance (to a tenth of a point) 95% of A/A runs pass
            "tolerance": math.ceil(max(0.0, _quantile([-lo for lo in lows], AA_PASS)) * 1000 - 1e-9)
            / 1000,
        }
        if target is not None:
            row["aa_pass_at_target"] = round(sum(lo >= -target for lo in lows) / len(lows), 4)
        rows.append(row)

    observed: List[Dict[str, Any]] = []
    for i in range(len(experiments)):
        for j in range(i + 1, len(experiments)):
            a, b = experiments[i], experiments[j]
            sa = {c: sets[i][c] for c in ids}
            sb = {c: sets[j][c] for c in ids}
            cmp = compare_runs(gate, sa, sb, baseline_id=a.experiment_id)
            t = next(t for t in cmp["tests"] if t["metric"] == PASS_METRIC)
            observed.append(
                {
                    "a": a.experiment_id,
                    "b": b.experiment_id,
                    "diff": t["diff"],
                    "ci_lo": t["ci_lo"],
                    "ci_hi": t["ci_hi"],
                }
            )

    metrics: Dict[str, Any] = {}
    for name in sorted({m for e in experiments for m in e.metrics}):
        means = [e.metrics[name]["mean"] for e in experiments if name in e.metrics]
        metrics[name] = {
            "means": means,
            "sd": round(stdev(means), 6) if len(means) > 1 else None,
        }

    flaky = [c for c in ids if 0 < passes[c] < trials[c]]
    at_configured = next(row for row in rows if row["repeats"] == configured)
    recommended: Optional[int] = configured
    notes: List[str] = []
    if len(shas) > 1:
        notes.append(
            f"the runs are of different commits {shas}: what moves between them is not "
            "only noise — calibrate runs of one commit"
        )
    if target is not None:
        reach = [row["repeats"] for row in rows if row["tolerance"] <= target]
        recommended = min(reach) if reach else None
        if recommended is None:
            best = min(rows, key=lambda row: row["tolerance"])
            notes.append(
                f"too noisy to gate at tolerance {target * 100:.1f} pts with {n} cases: the "
                f"best of the table ({best['repeats']} repeat(s)) needs "
                f"{best['tolerance'] * 100:.1f} pts. More cases "
                "narrow the interval (`operonx eval power` says how many); so does a larger "
                "tolerance"
            )
        elif at_configured["tolerance"] > target:
            notes.append(
                f"at the configured {configured} repeat(s) A/A runs pass at tolerance "
                f"{target * 100:.1f} pts only {at_configured['aa_pass_at_target']:.0%} of the "
                f"time; {recommended} repeats reach it"
            )
    if flaky:
        notes.append(
            f"{len(flaky)} of {n} cases flipped across the runs; a case that never flipped in "
            f"{min(trials.values())} trials is treated as deterministic, so more runs measure "
            "rare flakes better"
        )
    return {
        "eval": evals[0],
        "experiments": [e.experiment_id for e in experiments],
        "code_versions": shas,
        "runs": len(experiments),
        "cases": n,
        "repeats": configured,
        "alpha": alpha,
        "simulations": simulations,
        "flaky_share": round(len(flaky) / n, 6),
        "flaky_cases": flaky[:50],
        # P(two trials of a case disagree), averaged over cases
        "flip_rate": round(sum(2 * v for v in var.values()) / n, 6),
        "metrics": metrics,
        "rows": rows,
        "observed": observed,
        "target": target,
        "recommended_repeats": recommended,
        "suggested_tolerance": (
            next(row["tolerance"] for row in rows if row["repeats"] == recommended)
            if recommended is not None
            else None
        ),
        "notes": notes,
    }


def discordance(a: ExperimentData, b: ExperimentData) -> Tuple[float, int]:
    """The share of the cases two experiments share (unchanged) on which
    they differ — ``mean |s_B − s_A|`` of the ``pass`` share, which for one
    trial per case is the McNemar discordance ``(b + c) / n``. Returns
    ``(p_d, n)``."""
    oa, ob = a.outcomes(), b.outcomes()
    ids = [
        c
        for c in sorted(set(oa) & set(ob))
        if not (oa[c].case_hash and ob[c].case_hash and oa[c].case_hash != ob[c].case_hash)
    ]
    if not ids:
        raise ValueError(f"{a.experiment_id} and {b.experiment_id} share no unchanged case")
    diffs = [abs(ob[c].share(PASS_METRIC) - oa[c].share(PASS_METRIC)) for c in ids]  # type: ignore[operator]
    return sum(diffs) / len(diffs), len(ids)
