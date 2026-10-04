"""Judge alignment: how often a judge agrees with people.

A judge is only as good as its agreement with the humans it stands in
for. :func:`align` joins a judge's scores with human scores (``source=
human``) on the same targets — the same run (and op), or the same item of
an experiment — and measures, with PASS as the positive class::

    TPR       the judge says PASS when people do
    TNR       the judge says FAIL when people do (the failures it catches)
    accuracy  the share it agrees on
    κ         Cohen's kappa: agreement beyond what both raters' own PASS
              rates give by chance — (p_o − p_e) / (1 − p_e)

:func:`record_alignment` keeps the result as a score on the judge itself
(``target="evaluator"``, one per judge version), which is what an eval's
report reads: a judge that gates an eval with no record for its version,
or with κ < 0.6, is reported as ``UNVALIDATED JUDGE``. ``operonx eval
align <judge>`` does both.
"""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from operonx.telemetry.scores import Score, ScoreFilter, ScoreStore

from .stats import wilson, z_of

__all__ = [
    "ALIGNED_KAPPA",
    "Alignment",
    "align",
    "alignment_of",
    "alignments_of",
    "cohen_kappa",
    "describe",
    "record_alignment",
]

#: The κ from which a judge counts as aligned with its humans.
ALIGNED_KAPPA = 0.6

#: Human labels read as PASS or FAIL when a score has no ``passed``.
PASS_LABELS = frozenset({"pass", "good", "yes", "true"})
FAIL_LABELS = frozenset({"fail", "bad", "no", "false"})

#: How many disagreements an alignment keeps.
MAX_DISAGREEMENTS = 200


def cohen_kappa(tp: int, fp: int, fn: int, tn: int, confidence: float = 0.95) -> Dict[str, Any]:
    """The agreement statistics of a 2×2 table — *tp* judge PASS / human
    PASS, *fp* judge PASS / human FAIL, *fn* judge FAIL / human PASS, *tn*
    both FAIL. κ's standard error is Cohen's (1960) large-sample one,
    √(p_o(1 − p_o) / (n(1 − p_e)²)); TPR and TNR carry Wilson intervals.
    A statistic with no cases to stand on is ``None``, and κ is
    undefined when both raters gave one label only (p_e = 1)."""
    n = tp + fp + fn + tn
    out: Dict[str, Any] = {"n": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn}
    pos, neg = tp + fn, fp + tn
    out["tpr"] = tp / pos if pos else None
    out["tnr"] = tn / neg if neg else None
    out["tpr_ci"] = list(wilson(tp, pos, confidence)) if pos else None
    out["tnr_ci"] = list(wilson(tn, neg, confidence)) if neg else None
    out["accuracy"] = (tp + tn) / n if n else None
    out["kappa"] = out["kappa_se"] = out["kappa_ci"] = None
    out["note"] = None
    if not n:
        out["note"] = "no case was labelled by both the judge and a person"
        return out
    po = (tp + tn) / n
    pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / n**2
    if pe >= 1.0:
        out["note"] = (
            "κ is undefined: the judge and the people each gave one label only, so agreement "
            "cannot be told from chance — label cases of both kinds"
        )
        return out
    kappa = (po - pe) / (1 - pe)
    se = math.sqrt(po * (1 - po) / (n * (1 - pe) ** 2))
    z = z_of(confidence)
    out.update(
        kappa=kappa,
        kappa_se=se,
        kappa_ci=[max(-1.0, kappa - z * se), min(1.0, kappa + z * se)],
    )
    return out


@dataclass
class Alignment:
    """One judge version against human labels: the 2×2 table, the
    statistics of :func:`cohen_kappa`, what could not be paired, and the
    cases the judge and the people disagree on."""

    judge: str
    version: str
    human: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    unmatched_judge: int = 0
    unmatched_human: int = 0
    human_ties: int = 0
    unusable_human: int = 0
    disagreements: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.stats:
            self.stats = cohen_kappa(self.tp, self.fp, self.fn, self.tn)

    @classmethod
    def of_counts(
        cls, judge: str, version: str, human: str, tp: int, fp: int, fn: int, tn: int
    ) -> "Alignment":
        """An alignment from its table alone."""
        return cls(judge, version, human, tp, fp, fn, tn)

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def kappa(self) -> Optional[float]:
        return self.stats.get("kappa")

    @property
    def aligned(self) -> bool:
        return self.kappa is not None and self.kappa >= ALIGNED_KAPPA

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> Dict[str, Any]:
        """What the record keeps (and an eval's report shows)."""
        s = self.stats
        return {
            "judge": self.judge,
            "version": self.version,
            "human": self.human,
            "n": self.n,
            "kappa": s.get("kappa"),
            "kappa_se": s.get("kappa_se"),
            "kappa_ci": s.get("kappa_ci"),
            "tpr": s.get("tpr"),
            "tpr_ci": s.get("tpr_ci"),
            "tnr": s.get("tnr"),
            "tnr_ci": s.get("tnr_ci"),
            "accuracy": s.get("accuracy"),
            "aligned": self.aligned,
            "note": s.get("note"),
            "confusion": {"tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn},
            "unmatched_judge": self.unmatched_judge,
            "unmatched_human": self.unmatched_human,
            "human_ties": self.human_ties,
            "unusable_human": self.unusable_human,
        }


# ── pairing ──────────────────────────────────────────────────────────────


def _keys(s: Score) -> Tuple[Tuple[Any, ...], ...]:
    """What a score judged, as join keys: an op score is its op; a run's
    score is its run; an item's score is its item — and the run it is."""
    if s.target == "op":
        return (("op", s.trace_id, s.op_id),)
    if s.target == "trace":
        return (("trace", s.trace_id),)
    if s.target == "session":
        return (("session", s.session_id),)
    if s.target == "item":
        keys: List[Tuple[Any, ...]] = [("item", s.experiment_id, s.case_id, int(s.repeat or 0))]
        if s.trace_id:
            keys.append(("trace", s.trace_id))
        return tuple(keys)
    return ()


def _human_says(s: Score) -> Optional[bool]:
    """A human score as PASS (True) / FAIL (False), or ``None`` when it says neither."""
    if s.passed is not None:
        return bool(s.passed)
    if s.label is not None:
        text = str(s.label).strip().lower()
        if text in PASS_LABELS:
            return True
        if text in FAIL_LABELS:
            return False
        return None
    if s.value is not None:
        return float(s.value) >= 0.5
    return None


def _all(store: ScoreStore, where: ScoreFilter) -> List[Score]:
    return store.scores(where, limit=10_000_000)


def align(
    store: ScoreStore,
    judge: str,
    *,
    human: Optional[str] = None,
    version: Optional[str] = None,
    experiment: Optional[str] = None,
) -> Alignment:
    """*judge*'s scores (its score name, e.g. ``judge:polite``) against the
    human scores named *human* (default the same name), on the same
    targets. *version* picks the judge version (default its newest, by
    when it scored); *experiment* only that experiment's judge scores.
    Several people on one target: the majority; a tie is left out and
    counted, as are human labels that say neither PASS nor FAIL."""
    human = human or judge
    judged = _all(store, ScoreFilter(score_name=judge, source="judge"))
    judged = [s for s in judged if s.target != "pair" and s.passed is not None]
    if experiment is not None:
        judged = [s for s in judged if s.experiment_id == experiment]
    if version is None and judged:
        newest: Dict[str, float] = {}
        for s in judged:
            newest[s.evaluator_version] = max(newest.get(s.evaluator_version, 0.0), s.created_at)
        version = max(newest, key=lambda v: newest[v])
    judged = [s for s in judged if s.evaluator_version == version]

    labels: Dict[Tuple[Any, ...], Dict[str, Optional[bool]]] = defaultdict(dict)
    targets: Dict[Tuple[Any, ...], Tuple[Any, ...]] = {}  # every key → its target's first key
    unusable = 0
    for s in _all(store, ScoreFilter(score_name=human, source="human")):
        keys = _keys(s)
        if not keys:
            continue
        said = _human_says(s)
        if said is None:
            unusable += 1
        target = keys[0]
        for k in keys:
            targets.setdefault(k, target)
        labels[target][str(s.author)] = said

    out = Alignment(judge, str(version or ""), human, unusable_human=unusable)
    seen = set()
    for s in judged:
        hit = next((targets[k] for k in _keys(s) if k in targets), None)
        votes = Counter(v for v in labels.get(hit, {}).values() if v is not None) if hit else None
        if hit is not None:
            seen.add(hit)
        if not votes:
            out.unmatched_judge += 1
            continue
        if votes[True] == votes[False]:
            out.human_ties += 1
            continue
        says, people = bool(s.passed), votes[True] > votes[False]
        if says and people:
            out.tp += 1
        elif says:
            out.fp += 1
        elif people:
            out.fn += 1
        else:
            out.tn += 1
        if says != people and len(out.disagreements) < MAX_DISAGREEMENTS:
            out.disagreements.append(
                {
                    "target": list(hit),
                    "trace_id": s.trace_id,
                    "experiment_id": s.experiment_id,
                    "case_id": s.case_id,
                    "judge": "PASS" if says else "FAIL",
                    "human": "PASS" if people else "FAIL",
                    "reason": s.reason,
                }
            )
    out.unmatched_human = sum(1 for t in labels if t not in seen)
    out.stats = cohen_kappa(out.tp, out.fp, out.fn, out.tn)
    return out


# ── the record ───────────────────────────────────────────────────────────


def record_alignment(store: ScoreStore, alignment: Alignment) -> Score:
    """Keep *alignment* as a score on the judge: ``target="evaluator"``,
    ``value`` κ, ``passed`` aligned (κ ≥ 0.6). Its id is the judge and
    version, so measuring the same version again replaces the record."""
    s = alignment.summary()
    k = alignment.kappa
    reason = (
        f"κ = {k:.2f} over {alignment.n} human-labelled cases" if k is not None else str(s["note"])
    )
    score = Score(
        score_name=alignment.judge,
        target="evaluator",
        source="code",
        data_type="numeric",
        value=k,
        passed=alignment.aligned,
        reason=reason,
        evaluator_version=alignment.version,
        metadata={**s, "recorded_at": time.time(), "disagreements": alignment.disagreements[:50]},
    )
    store.put_scores([score])
    return score


def alignments_of(store: ScoreStore, judge: str) -> Dict[str, Dict[str, Any]]:
    """Every alignment record of *judge*, by version (the newest per version)."""
    out: Dict[str, Tuple[float, Dict[str, Any]]] = {}
    for s in _all(store, ScoreFilter(score_name=judge, target="evaluator")):
        if s.evaluator_version not in out or s.created_at >= out[s.evaluator_version][0]:
            out[s.evaluator_version] = (s.created_at, dict(s.metadata or {}))
    return {v: m for v, (_, m) in out.items()}


def alignment_of(store: ScoreStore, judge: str, version: str) -> Optional[Dict[str, Any]]:
    """The alignment record of *judge* at *version*, or ``None``."""
    return alignments_of(store, judge).get(version)


def describe(rec: Mapping[str, Any]) -> str:
    """A record in one line: κ, TPR, TNR, n."""

    def f(x: Any) -> str:
        return "–" if x is None else f"{x:.2f}"

    return f"κ = {f(rec.get('kappa'))}, TPR {f(rec.get('tpr'))}, TNR {f(rec.get('tnr'))}, n = {rec.get('n')}"
