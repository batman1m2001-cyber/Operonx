"""The gate: what an eval run's numbers mean for CI.

Five verdicts, worst first, each with its exit code::

    error         3   infrastructure: too many trials errored, or the run
                      did not judge every case (the source broke, it stopped)
    regressed     1   worse than the baseline — confidently, and by more than
                      the tolerance — or a must-pass case that passed there
                      now fails
    failed        1   an absolute threshold missed (the 1.9.0 gate), or a
                      must-pass case fails with no baseline to compare to
    inconclusive  0   the data cannot rule out a drop larger than the
                      tolerance; 2 under ``strict``
    pass          0

Against a baseline, each **gated** metric (``pass`` unless said otherwise)
is compared on the cases both runs share — paired, so each case is its own
control (:func:`~operonx.app.evals.stats.compare_paired`). The gated
metrics' p-values are Holm-adjusted; every other check is compared too and
reported with Benjamini–Hochberg q-values, as exploratory — it never gates.
For a gated metric with difference ``d = B − A``::

    d < −tolerance and p_holm < alpha      → regressed
    ci_lo(d) ≥ −tolerance                  → pass
    otherwise                              → inconclusive
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from .stats import Estimate, benjamini_hochberg, compare_paired, holm, seed_of

__all__ = [
    "CaseOutcome",
    "EXIT_CODES",
    "Gate",
    "PASS_METRIC",
    "VERDICTS",
    "decide",
    "outcomes",
]

#: The metric every eval has: a trial passes when every check passes.
PASS_METRIC = "pass"

PASS, INCONCLUSIVE, FAILED, REGRESSED, ERROR = (
    "pass",
    "inconclusive",
    "failed",
    "regressed",
    "error",
)
#: Best to worst; a run's verdict is the worst of its findings.
VERDICTS = (PASS, INCONCLUSIVE, FAILED, REGRESSED, ERROR)
EXIT_CODES = {PASS: 0, INCONCLUSIVE: 0, FAILED: 1, REGRESSED: 1, ERROR: 3}
STRICT_INCONCLUSIVE_EXIT = 2

STABLE_PASS, STABLE_FAIL, FLAKY = "stable_pass", "stable_fail", "flaky"

#: How many case ids a flip class lists in run.json (the counts are whole).
FLIP_LIST_MAX = 50

#: Below this many resampled units (cases, or clusters) a percentile
#: bootstrap interval runs narrow, and the report says so.
BOOTSTRAP_MIN_UNITS = 30


# ── per-case outcomes ────────────────────────────────────────────────────


@dataclass
class CaseOutcome:
    """Every trial of one case: whether it passed, per check, and errors."""

    case: str
    case_hash: Optional[str] = None
    cluster: Optional[str] = None
    tags: Tuple[str, ...] = ()
    passed: List[bool] = field(default_factory=list)
    checks: Dict[str, List[bool]] = field(default_factory=dict)
    errored: int = 0

    def share(self, metric: str) -> Optional[float]:
        """The share of this case's trials that passed *metric* (``pass``
        or a check's name); ``None`` when no trial measured it."""
        got = self.passed if metric == PASS_METRIC else self.checks.get(metric)
        return sum(got) / len(got) if got else None

    @property
    def stability(self) -> str:
        if all(self.passed):
            return STABLE_PASS
        return STABLE_FAIL if not any(self.passed) else FLAKY


def outcomes(trials: Iterable[Mapping[str, Any]]) -> Dict[str, CaseOutcome]:
    """Trials grouped by case. A trial is ``{case, passed, checks, error,
    case_hash?, cluster?, tags?}``; ``checks`` maps a name to a bool."""
    out: Dict[str, CaseOutcome] = {}
    for t in trials:
        key = str(t["case"])
        o = out.get(key)
        if o is None:
            o = out[key] = CaseOutcome(
                key,
                case_hash=t.get("case_hash"),
                cluster=t.get("cluster"),
                tags=tuple(t.get("tags") or ()),
            )
        o.passed.append(bool(t["passed"]))
        for name, ok in (t.get("checks") or {}).items():
            o.checks.setdefault(name, []).append(bool(ok))
        o.errored += int(bool(t.get("error")))
    return out


def trials_of_items(items: Iterable[Any]) -> List[Dict[str, Any]]:
    """A recorded run's items as trials — for a baseline. A 1.9.0 record
    has no ``case`` on its verdicts: the item key is the case."""
    out = []
    for item in items:
        v = getattr(item, "verdict", None)
        if not v:
            continue
        out.append(
            {
                "case": v.get("case", item.key),
                "passed": v.get("passed", False),
                "checks": {k: c.get("passed", False) for k, c in (v.get("checks") or {}).items()},
                "error": bool(v.get("error")),
                "case_hash": v.get("case_hash"),
                "cluster": v.get("cluster"),
                "tags": v.get("tags") or (),
            }
        )
    return out


# ── the gate ─────────────────────────────────────────────────────────────

PerMetric = Union[float, Mapping[str, float], None]


def _per_metric(value: PerMetric, what: str) -> Dict[str, float]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(k): float(v) for k, v in value.items()}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {PASS_METRIC: float(value)}
    raise TypeError(f"Gate({what}=…) takes a number or {{metric: number}}, not {value!r}")


@dataclass
class Gate:
    """When an eval run passes, as data. See the module docstring.

    Args:
        threshold: Absolute floor(s): a number for the ``pass`` rate, or
            ``{metric: floor}``. A metric under its floor fails the run —
            exactly ``Eval(threshold=…)``'s 1.9.0 rule.
        baseline: What to compare against: ``"latest"`` (this eval's last
            finished run, fixed when the run starts) or a run id under its
            ``record_dir``.
        tolerance: How large a drop matters, per gated metric (a number,
            or ``{metric: number}``). Required with a ``baseline``.
        metrics: The metrics compared against the baseline that can gate
            (default ``["pass"]``); the other checks are compared too, as
            exploratory.
        must_pass_tag: Cases with this tag are a deterministic tier (see
            the module docstring). ``None`` turns it off.
        max_error_rate: The share of trials that may error (failed or
            timed out) before the run is an infrastructure ``error``.
        alpha: The significance level, after Holm adjustment.
        strict: An ``inconclusive`` run exits 2 instead of 0.
        bootstrap: Bootstrap replicates for each comparison's CI.
    """

    threshold: PerMetric = None
    baseline: Optional[str] = None
    tolerance: PerMetric = None
    metrics: Optional[Sequence[str]] = None
    must_pass_tag: Optional[str] = "critical"
    max_error_rate: float = 0.05
    alpha: float = 0.05
    strict: bool = False
    bootstrap: int = 2000

    def __post_init__(self) -> None:
        self._thresholds = _per_metric(self.threshold, "threshold")
        self._tolerances = _per_metric(self.tolerance, "tolerance")
        for name, t in self._thresholds.items():
            if not 0 <= t <= 1:
                raise ValueError(f"Gate: threshold for {name!r} is a pass rate in [0, 1], not {t}")
        for name, t in self._tolerances.items():
            if not 0 <= t <= 1:
                raise ValueError(f"Gate: tolerance for {name!r} is a drop in [0, 1], not {t}")
        if self.baseline is not None:
            text = str(self.baseline)
            if text in ("main",) or text.startswith("git:"):
                raise ValueError(
                    f"Gate(baseline={text!r}) needs the experiment store, which does not "
                    "exist yet; use 'latest' or a run id from this eval's record_dir"
                )
            missing = [m for m in self.gated() if m not in self._tolerances]
            if missing:
                raise ValueError(
                    f"Gate(baseline={text!r}) needs a tolerance for {missing}: how large a "
                    "drop matters, e.g. tolerance=0.03 (3 points). A default of 0 would "
                    "call nearly every comparison inconclusive, and any other default is a guess"
                )
        if not 0 <= self.max_error_rate <= 1:
            raise ValueError("Gate: max_error_rate is a share of trials in [0, 1]")
        if not 0 < self.alpha < 1:
            raise ValueError("Gate: alpha is a significance level in (0, 1)")
        if int(self.bootstrap) < 100:
            raise ValueError("Gate: bootstrap wants at least 100 replicates")

    def gated(self) -> List[str]:
        """The metrics a baseline comparison may fail the run on."""
        return list(self.metrics) if self.metrics else [PASS_METRIC]

    def thresholds(self) -> Dict[str, float]:
        return dict(self._thresholds)

    def tolerance_for(self, metric: str) -> float:
        return self._tolerances[metric]

    def named_metrics(self) -> List[str]:
        """Every metric name the gate mentions, to check against the eval's."""
        names = set(self._thresholds) | set(self._tolerances)
        if self.metrics:
            names |= set(self.metrics)
        return sorted(names)

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> "Gate":
        """A Gate from a manifest's ``[job.gate]`` table."""
        known = {f for f in cls.__dataclass_fields__}
        unknown = sorted(set(options) - known)
        if unknown:
            raise ValueError(f"[job.gate] has keys a Gate does not read: {unknown}")
        return cls(**dict(options))

    def describe(self) -> Dict[str, Any]:
        out = {
            "threshold": self.threshold,
            "baseline": self.baseline,
            "tolerance": self.tolerance,
            "metrics": self.gated(),
            "must_pass_tag": self.must_pass_tag,
            "max_error_rate": self.max_error_rate,
            "alpha": self.alpha,
            "strict": self.strict,
        }
        return {k: v for k, v in out.items() if v is not None}


def exit_code(verdict: str, strict: bool = False) -> int:
    if verdict == INCONCLUSIVE and strict:
        return STRICT_INCONCLUSIVE_EXIT
    return EXIT_CODES[verdict]


def _worst(verdicts: Iterable[str]) -> str:
    return max(verdicts, key=VERDICTS.index, default=PASS)


# ── comparing two runs ───────────────────────────────────────────────────


def _flips(
    base: Mapping[str, CaseOutcome], cur: Mapping[str, CaseOutcome], ids: Sequence[str]
) -> Dict[str, Any]:
    """Cases that changed, classed by stability across repeats."""
    classes: Dict[str, List[str]] = {
        "regressed": [],
        "fixed": [],
        "destabilised": [],
        "stabilised": [],
    }
    for c in ids:
        a, b = base[c].stability, cur[c].stability
        if a == STABLE_PASS and b == STABLE_FAIL:
            classes["regressed"].append(c)
        elif a == STABLE_FAIL and b == STABLE_PASS:
            classes["fixed"].append(c)
        elif a == STABLE_PASS and b != STABLE_PASS:
            classes["destabilised"].append(c)
        elif a != STABLE_PASS and b == STABLE_PASS:
            classes["stabilised"].append(c)
    out: Dict[str, Any] = {k: len(v) for k, v in classes.items()}
    out["cases"] = {k: v[:FLIP_LIST_MAX] for k, v in classes.items() if v}
    # one trial each side cannot tell a flip from a flake
    out["verified"] = all(len(base[c].passed) > 1 and len(cur[c].passed) > 1 for c in ids)
    return out


def compare_runs(
    gate: Gate,
    base: Mapping[str, CaseOutcome],
    cur: Mapping[str, CaseOutcome],
    *,
    baseline_id: str,
) -> Dict[str, Any]:
    """Every metric of *cur* against *base*, on the cases both hold with
    the same ``case_hash``; gated metrics judged, the rest exploratory."""
    shared = sorted(set(base) & set(cur))
    changed = [
        c
        for c in shared
        if base[c].case_hash and cur[c].case_hash and base[c].case_hash != cur[c].case_hash
    ]
    ids = [c for c in shared if c not in set(changed)]
    names = [PASS_METRIC] + sorted({m for o in cur.values() for m in o.checks})
    gated = set(gate.gated())
    tests: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for metric in names:
        rows = [(c, base[c].share(metric), cur[c].share(metric)) for c in ids]
        rows = [r for r in rows if r[1] is not None and r[2] is not None]
        if not rows:
            skipped.append(metric)
            continue
        a = [r[1] for r in rows]
        b = [r[2] for r in rows]
        clusters = [cur[r[0]].cluster or r[0] for r in rows]
        seed = seed_of("gate", baseline_id, metric, json.dumps(rows))
        comp = compare_paired(
            a, b, clusters, replicates=int(gate.bootstrap), seed=seed, confidence=1 - gate.alpha
        )
        tests.append({"metric": metric, "gated": metric in gated, **comp.as_dict()})

    for group, adjust, key in (
        ([t for t in tests if t["gated"]], holm, "p_holm"),
        ([t for t in tests if not t["gated"]], benjamini_hochberg, "q_bh"),
    ):
        for t, adj in zip(group, adjust([t["p"] for t in group])):
            t[key] = round(adj, 6)

    for t in tests:
        if t["gated"]:
            tol = gate.tolerance_for(t["metric"])
            if t["diff"] < -tol and t["p_holm"] < gate.alpha:
                t["verdict"] = REGRESSED
            elif t["ci_lo"] >= -tol:
                t["verdict"] = PASS
            else:
                t["verdict"] = INCONCLUSIVE
            t["tolerance"] = tol
        else:
            t["significant"] = t["q_bh"] < gate.alpha
    return {
        "cases": len(ids),
        "changed_cases": len(changed),
        "only_in_baseline": len(set(base) - set(cur)),
        "only_in_this_run": len(set(cur) - set(base)),
        "tests": tests,
        "not_compared": skipped,
        "flips": _flips(base, cur, ids),
    }


# ── the decision ─────────────────────────────────────────────────────────


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def decide(
    gate: Gate,
    *,
    current: Mapping[str, CaseOutcome],
    metrics: Mapping[str, Estimate],
    complete: bool,
    baseline: Optional[Tuple[str, Mapping[str, CaseOutcome], Optional[Mapping]]] = None,
    fingerprint: Optional[Mapping] = None,
) -> Dict[str, Any]:
    """The gate's verdict on one run, with every reason it found.

    *baseline* is ``(run_id, outcomes, fingerprint)``, or ``None`` when
    there is none (the comparison is then skipped and said so).
    """
    found: List[str] = []
    reasons: List[str] = []
    warnings: List[str] = []
    trials = sum(len(o.passed) for o in current.values())
    errored = sum(o.errored for o in current.values())
    error_rate = errored / trials if trials else 0.0

    if not complete:
        found.append(ERROR)
        reasons.append(
            "the run did not reach every case (its source broke or it was stopped): "
            "the numbers describe part of the dataset"
        )
    if error_rate > gate.max_error_rate:
        found.append(ERROR)
        reasons.append(
            f"{errored} of {trials} trials errored ({_pct(error_rate)} > max_error_rate "
            f"{_pct(gate.max_error_rate)}): an infrastructure failure, not a quality verdict"
        )

    for metric, floor in gate.thresholds().items():
        est = metrics.get(metric)
        if est is None:
            found.append(FAILED)
            reasons.append(
                f"{metric}: no case measured it, so its threshold {_pct(floor)} is unmet"
            )
        elif est.mean < floor:
            found.append(FAILED)
            reasons.append(f"{metric}: {_pct(est.mean)} < threshold {_pct(floor)}")

    base_outcomes = baseline[1] if baseline else None
    must: Dict[str, Any] = {}
    if gate.must_pass_tag:
        tagged = [o for o in current.values() if gate.must_pass_tag in o.tags]
        broken = []
        for o in tagged:
            if o.stability == FLAKY:
                warnings.append(
                    f"must-pass case {o.case!r} is flaky: {sum(o.passed)}/{len(o.passed)} passed"
                )
            if o.stability != STABLE_FAIL:
                continue
            if base_outcomes is None:
                broken.append(o.case)
            elif o.case in base_outcomes and base_outcomes[o.case].stability == STABLE_PASS:
                broken.append(o.case)
        if broken:
            found.append(REGRESSED if base_outcomes is not None else FAILED)
            what = "passed in the baseline and fails" if base_outcomes is not None else "fails"
            reasons.append(f"must-pass ({gate.must_pass_tag!r}) {what}: {broken}")
        must = {"tag": gate.must_pass_tag, "cases": len(tagged), "broken": broken}

    comparison: Optional[Dict[str, Any]] = None
    if gate.baseline is not None:
        if baseline is None:
            warnings.append(
                f"baseline {gate.baseline!r}: no earlier finished run yet; "
                "judged on thresholds only"
            )
        else:
            base_id, base_cases, base_fp = baseline
            comparison = compare_runs(gate, base_cases, current, baseline_id=base_id)
            comparison["baseline"] = base_id
            if not base_fp:
                warnings.append(
                    f"baseline {base_id} has no fingerprint: comparable only by case id"
                )
            elif fingerprint:
                for k in ("dataset_version", "evaluators_hash"):
                    if base_fp.get(k) != fingerprint.get(k):
                        warnings.append(
                            f"{k} differs from baseline {base_id}: compared on the "
                            f"{comparison['cases']} shared, unchanged cases"
                        )
            for t in comparison["tests"]:
                if not t["gated"]:
                    continue
                found.append(t["verdict"])
                if t["verdict"] != PASS:
                    reasons.append(
                        f"{t['metric']}: {t['diff'] * 100:+.1f} pts "
                        f"[{t['ci_lo'] * 100:+.1f}, {t['ci_hi'] * 100:+.1f}] vs {base_id}, "
                        f"p={t['p_holm']:.3g} ({t['method']}, Holm), tolerance "
                        f"{t['tolerance'] * 100:.1f} pts → {t['verdict']}"
                    )
            for t in comparison["tests"]:
                if t.get("units") is not None and t["units"] < BOOTSTRAP_MIN_UNITS:
                    warnings.append(
                        f"{t['metric']}: bootstrap over {t['units']} units; under "
                        f"{BOOTSTRAP_MIN_UNITS} its interval runs narrow — read it as indicative"
                    )
            for m in gate.gated():
                if m in comparison["not_compared"]:
                    found.append(INCONCLUSIVE)
                    reasons.append(f"{m}: no shared case measured it in both runs")

    verdict = _worst(found)
    out: Dict[str, Any] = {
        "verdict": verdict,
        "exit_code": exit_code(verdict, gate.strict),
        "reasons": reasons,
        "warnings": warnings,
        "strict": gate.strict,
        "error_rate": round(error_rate, 6),
        "gate": gate.describe(),
    }
    if must:
        out["must_pass"] = must
    if comparison is not None:
        out["comparison"] = comparison
    return out
