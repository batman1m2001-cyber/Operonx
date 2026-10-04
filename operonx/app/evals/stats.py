"""The statistics an eval reports and gates on, in pure Python.

Core operonx has no numpy, and none of this needs it: every function here
works on plain lists of floats and is deterministic — the bootstrap takes
a seed, and :func:`seed_of` derives one from text, never from ``hash()``
(which Python salts per process).

What each answers:

* :func:`wilson` — a pass rate's 95% interval that behaves at 0, 1 and
  small n (45/50 → [0.786, 0.957]).
* :func:`mean_se` / :func:`clustered_se` — the standard error of a mean,
  naive or with cases grouped (turns of one conversation, one scenario's
  variants), which can be several times the naive one.
* :func:`estimate` — picks the right one of the three for a metric.
* :func:`pass_hat_k` — the chance that k trials of a case all pass.
* :func:`mcnemar` / :func:`newcombe_paired` — the exact paired test and
  the paired interval for a binary check.
* :func:`paired_bootstrap` — a CI and p for the mean paired difference,
  resampling cases or whole clusters.
* :func:`compare_paired` — picks McNemar or the bootstrap for one metric.
* :func:`holm` / :func:`benjamini_hochberg` — adjusted p-values for
  several metrics at once (gated, and exploratory).
* :func:`paired_sample_size` / :func:`detectable_drop` — how many cases
  a drop needs, and what drop a number of cases can see.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import asdict, dataclass
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "Comparison",
    "Estimate",
    "benjamini_hochberg",
    "clustered_se",
    "compare_paired",
    "detectable_drop",
    "estimate",
    "holm",
    "mcnemar",
    "mean_se",
    "newcombe_paired",
    "paired_bootstrap",
    "paired_sample_size",
    "pass_hat_k",
    "seed_of",
    "wilson",
    "z_of",
]


def z_of(confidence: float = 0.95) -> float:
    """The two-sided normal quantile: 1.959964 for 95%."""
    if not 0 < confidence < 1:
        raise ValueError(f"confidence is a probability in (0, 1), not {confidence!r}")
    return NormalDist().inv_cdf(1 - (1 - confidence) / 2)


def seed_of(*parts: Any) -> int:
    """A 64-bit seed from text: the same parts give the same seed in
    every process (``hash()`` would not)."""
    text = "\x1f".join(str(p) for p in parts)
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)


# ── one experiment ───────────────────────────────────────────────────────


def wilson(successes: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """The Wilson score interval for ``successes / n``."""
    if n <= 0:
        raise ValueError("wilson() needs at least one trial")
    if not 0 <= successes <= n:
        raise ValueError(f"wilson(): {successes} successes out of {n} trials")
    z = z_of(confidence)
    p = successes / n
    z2n = z * z / n
    center = (p + z2n / 2) / (1 + z2n)
    half = z * math.sqrt(p * (1 - p) / n + z2n / (4 * n)) / (1 + z2n)
    # at 0 and n the bound is exactly 0 (or 1); the formula leaves float dust
    lo = 0.0 if successes == 0 else max(0.0, center - half)
    hi = 1.0 if successes == n else min(1.0, center + half)
    return lo, hi


def mean_se(values: Sequence[float]) -> Tuple[float, Optional[float]]:
    """The mean and its CLT standard error (sample SD / √n); the SE is
    ``None`` below two values, where it is not defined."""
    n = len(values)
    if n == 0:
        raise ValueError("mean_se() of no values")
    mean = math.fsum(values) / n
    if n < 2:
        return mean, None
    var = math.fsum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(var / n)


def clustered_se(values: Sequence[float], clusters: Sequence[Any]) -> float:
    """The cluster-robust standard error of the mean:
    ``sqrt(Σ_c (Σ_{i∈c} (s_i − s̄))²) / n``.

    Cases in one cluster move together, so they carry less information
    than as many independent cases; this is the SE that says so.
    """
    if len(values) != len(clusters):
        raise ValueError("clustered_se(): one cluster per value")
    n = len(values)
    if n == 0:
        raise ValueError("clustered_se() of no values")
    mean = math.fsum(values) / n
    sums: Dict[Any, float] = {}
    for v, c in zip(values, clusters):
        sums[c] = sums.get(c, 0.0) + (v - mean)
    return math.sqrt(math.fsum(s * s for s in sums.values())) / n


@dataclass(frozen=True)
class Estimate:
    """A metric's mean with its uncertainty, and how that was computed."""

    n: int
    mean: float
    se: Optional[float]
    ci_lo: Optional[float]
    ci_hi: Optional[float]
    method: str  # "wilson" | "clt" | "clustered"

    def as_dict(self, digits: int = 6) -> Dict[str, Any]:
        out = asdict(self)
        for k in ("mean", "se", "ci_lo", "ci_hi"):
            if out[k] is not None:
                out[k] = round(out[k], digits)
        return out


def _is_binary(values: Sequence[float]) -> bool:
    return all(v in (0, 1) for v in values)


def _clustered(clusters: Optional[Sequence[Any]]) -> bool:
    return clusters is not None and len(set(clusters)) < len(clusters)


def estimate(
    values: Sequence[float],
    clusters: Optional[Sequence[Any]] = None,
    *,
    confidence: float = 0.95,
    bounds: Optional[Tuple[float, float]] = None,
) -> Estimate:
    """The mean of per-case *values* with a confidence interval.

    Grouped cases (some cluster holds more than one) → the clustered SE;
    0/1 values otherwise → Wilson; anything else → the CLT. *bounds*
    clips a normal interval to the values' range (a pass rate's [0, 1]).
    """
    n = len(values)
    if n == 0:
        raise ValueError("estimate() of no values")
    z = z_of(confidence)
    if _clustered(clusters):
        mean = math.fsum(values) / n
        se: Optional[float] = clustered_se(values, clusters)  # type: ignore[arg-type]
        method = "clustered"
    elif _is_binary(values):
        k = int(sum(values))
        lo, hi = wilson(k, n, confidence)
        _, se = mean_se(values)
        return Estimate(n, k / n, se, lo, hi, "wilson")
    else:
        mean, se = mean_se(values)
        method = "clt"
    if se is None:
        return Estimate(n, mean, None, None, None, method)
    lo, hi = mean - z * se, mean + z * se
    if bounds is not None:
        lo, hi = max(bounds[0], lo), min(bounds[1], hi)
    return Estimate(n, mean, se, lo, hi, method)


def pass_hat_k(passes: int, trials: int, k: int) -> float:
    """pass^k: the chance that *k* trials drawn from a case's *trials*
    all pass, given *passes* of them did — ``C(c, k) / C(n, k)``, the
    unbiased estimate (τ-bench). 4 passes of 5 → pass^3 = 0.4."""
    if not 1 <= k <= trials:
        raise ValueError(f"pass^{k} needs 1 ≤ k ≤ trials ({trials})")
    if not 0 <= passes <= trials:
        raise ValueError(f"pass_hat_k(): {passes} passes out of {trials} trials")
    return math.comb(passes, k) / math.comb(trials, k)


# ── two experiments ──────────────────────────────────────────────────────


def newcombe_paired(
    both: int, went_bad: int, went_good: int, neither: int, confidence: float = 0.95
) -> Tuple[float, float]:
    """The CI for B's pass rate minus A's on the same cases (Newcombe 1998,
    method 10: Wilson intervals for each rate, joined by the correlation
    of the two runs). *both* passed in A and B, *went_bad* passed only in
    A, *went_good* only in B, *neither* in neither.

    Unlike a bootstrap it stays honest with few discordant cases: three
    cases that agree in both runs give about ±56 points, not [0, 0]. A
    positive correlation is shrunk by n/2 first (Newcombe's correction):
    without it, when most cases always pass or always fail, the interval
    covered a 1.5-point drop only 81% of the time at n = 40 (measured);
    with it, 99.9%.
    """
    n = both + went_bad + went_good + neither
    if n <= 0:
        raise ValueError("newcombe_paired() of no cases")
    p1, p2 = (both + went_bad) / n, (both + went_good) / n  # A's rate, B's rate
    l1, u1 = wilson(both + went_bad, n, confidence)
    l2, u2 = wilson(both + went_good, n, confidence)
    margins = (both + went_bad) * (went_good + neither) * (both + went_good) * (went_bad + neither)
    agree = both * neither - went_bad * went_good
    if agree > 0:
        agree = max(agree - n / 2, 0.0)
    phi = agree / math.sqrt(margins) if margins else 0.0
    d = p1 - p2  # Newcombe's direction, A − B
    lo = d - math.sqrt(max(0.0, (p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2))
    hi = d + math.sqrt(max(0.0, (u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2))
    return -hi, -lo


def mcnemar(b: int, c: int) -> float:
    """The exact two-sided McNemar test on the discordant pairs: *b*
    cases went pass → fail, *c* fail → pass.
    ``p = min(1, 2·P(X ≤ min(b, c)))`` with ``X ~ Bin(b + c, ½)``."""
    if b < 0 or c < 0:
        raise ValueError("mcnemar() counts cases; they cannot be negative")
    m = b + c
    if m == 0:
        return 1.0
    tail = sum(math.comb(m, i) for i in range(min(b, c) + 1))
    return min(1.0, 2 * tail / 2**m)


def _binomial(n: int, p: float, rng: random.Random) -> int:
    """One exact draw from Bin(n, p): inversion that walks outward from
    the mode, so it costs O(√(np(1−p))) and never underflows."""
    if n <= 0 or p <= 0.0:
        return 0
    if p >= 1.0:
        return n
    q = 1.0 - p
    mode = min(n, int((n + 1) * p))
    pm = math.exp(
        math.lgamma(n + 1)
        - math.lgamma(mode + 1)
        - math.lgamma(n - mode + 1)
        + mode * math.log(p)
        + (n - mode) * math.log(q)
    )
    u = rng.random() - pm
    if u <= 0:
        return mode
    r = p / q
    lo = hi = mode
    p_lo = p_hi = pm
    while lo > 0 or hi < n:
        if hi < n:
            p_hi *= (n - hi) / (hi + 1) * r
            hi += 1
            u -= p_hi
            if u <= 0:
                return hi
        if lo > 0:
            p_lo *= lo / ((n - lo + 1) * r)
            lo -= 1
            u -= p_lo
            if u <= 0:
                return lo
    # every outcome was visited and rounding left u a hair above zero
    return mode


def _multinomial(n: int, probs: Sequence[float], rng: random.Random) -> List[int]:
    """One draw of category counts: sequential conditional binomials."""
    counts: List[int] = []
    rest, mass = n, 1.0
    for p in probs[:-1]:
        k = _binomial(rest, min(1.0, p / mass), rng) if rest and mass > 0 else 0
        counts.append(k)
        rest -= k
        mass -= p
    counts.append(rest)
    return counts


def _bootstrap_means(
    units: Sequence[Tuple[float, int]], replicates: int, rng: random.Random
) -> List[float]:
    """Sorted bootstrap means of ``Σ sum / Σ size`` over *units* resampled
    with replacement — each unit a case ``(d, 1)`` or a cluster
    ``(Σ d, size)``.

    Equal units are one category, and one replicate is one multinomial
    draw over the categories: the same distribution as drawing n units,
    at a cost set by how many *distinct* units there are. Paired pass/fail
    differences have three (−1, 0, 1). When most units are distinct
    (continuous scores) drawing the n units directly is the cheaper way
    to the same distribution, and is what runs.
    """
    tally: Dict[Tuple[float, int], int] = {}
    for u in units:
        tally[u] = tally.get(u, 0) + 1
    cats = sorted(tally)  # input order must not change the draws
    n = len(units)
    out = []
    if len(cats) * 4 > n:
        ordered = sorted(units)
        for _ in range(replicates):
            drawn = rng.choices(ordered, k=n)
            out.append(math.fsum(u[0] for u in drawn) / sum(u[1] for u in drawn))
    else:
        probs = [tally[c] / n for c in cats]
        for _ in range(replicates):
            counts = _multinomial(n, probs, rng)
            total = math.fsum(k * c[0] for k, c in zip(counts, cats))
            size = sum(k * c[1] for k, c in zip(counts, cats))
            out.append(total / size)
    out.sort()
    return out


def _unit(value: float) -> float:
    """A difference as a resampling category: ``2/3 − 1/3`` and ``1/3 − 0``
    differ in the last bit and are the same unit."""
    return round(float(value), 12)


def paired_bootstrap(
    diffs: Sequence[float],
    clusters: Optional[Sequence[Any]] = None,
    *,
    replicates: int = 2000,
    seed: int = 0,
    confidence: float = 0.95,
) -> Tuple[float, float, float]:
    """``(ci_lo, ci_hi, p)`` for the mean of per-case differences.

    Resamples cases, or whole clusters when *clusters* groups them. The
    CI is the percentile interval; p is two-sided against "no change",
    read off the same replicates (so ``p < 1 − confidence`` exactly when
    the CI leaves out 0, up to the ``+1`` that keeps p above zero).
    """
    if not diffs:
        raise ValueError("paired_bootstrap() of no differences")
    if replicates < 100:
        raise ValueError("paired_bootstrap(): use at least 100 replicates")
    if clusters is not None:
        if len(clusters) != len(diffs):
            raise ValueError("paired_bootstrap(): one cluster per difference")
        groups: Dict[Any, List[float]] = {}
        for d, c in zip(diffs, clusters):
            groups.setdefault(c, []).append(d)
        units = [(_unit(math.fsum(g)), len(g)) for g in groups.values()]
    else:
        units = [(_unit(d), 1) for d in diffs]
    reps = _bootstrap_means(units, replicates, random.Random(seed))
    alpha = 1 - confidence
    lo = reps[max(0, math.floor(replicates * alpha / 2))]
    hi = reps[min(replicates - 1, math.ceil(replicates * (1 - alpha / 2)) - 1)]
    below = sum(1 for r in reps if r <= 0)
    above = sum(1 for r in reps if r >= 0)
    p = min(1.0, 2 * min(below + 1, above + 1) / (replicates + 1))
    return lo, hi, p


@dataclass(frozen=True)
class Comparison:
    """One metric, baseline A against candidate B, on the same cases."""

    n: int
    a: float
    b: float
    diff: float
    ci_lo: float
    ci_hi: float
    p: float
    method: str  # "mcnemar" | "bootstrap" | "bootstrap-clustered"
    corr: Optional[float] = None  # of A and B per case: why paired is tighter
    regressed: Optional[int] = None  # binary: pass → fail
    fixed: Optional[int] = None  # binary: fail → pass
    units: Optional[int] = None  # bootstrap: what was resampled (cases or clusters)

    def as_dict(self, digits: int = 6) -> Dict[str, Any]:
        out = asdict(self)
        for k in ("a", "b", "diff", "ci_lo", "ci_hi", "p", "corr"):
            if out[k] is not None:
                out[k] = round(out[k], digits)
        return {k: v for k, v in out.items() if v is not None}


def _corr(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    n = len(a)
    ma, mb = math.fsum(a) / n, math.fsum(b) / n
    sab = math.fsum((x - ma) * (y - mb) for x, y in zip(a, b))
    saa = math.fsum((x - ma) ** 2 for x in a)
    sbb = math.fsum((y - mb) ** 2 for y in b)
    return sab / math.sqrt(saa * sbb) if saa > 0 and sbb > 0 else None


def compare_paired(
    a: Sequence[float],
    b: Sequence[float],
    clusters: Optional[Sequence[Any]] = None,
    *,
    replicates: int = 2000,
    seed: int = 0,
    confidence: float = 0.95,
) -> Comparison:
    """Compare per-case values of the same cases under A and B.

    0/1 values in independent cases → the exact McNemar test and the
    Newcombe interval (:func:`newcombe_paired`), both from the 2×2 table.
    Otherwise (shares over repeats, or grouped cases) → the paired
    bootstrap's p and percentile interval, resampling cases or clusters.
    """
    if len(a) != len(b):
        raise ValueError("compare_paired(): A and B must hold the same cases")
    if not a:
        raise ValueError("compare_paired() of no cases")
    n = len(a)
    mean_a, mean_b = math.fsum(a) / n, math.fsum(b) / n
    grouped = _clustered(clusters)
    if not grouped and _is_binary(a) and _is_binary(b):
        table = {(1, 1): 0, (1, 0): 0, (0, 1): 0, (0, 0): 0}
        for x, y in zip(a, b):
            table[int(x), int(y)] += 1
        went_bad, went_good = table[1, 0], table[0, 1]
        lo, hi = newcombe_paired(table[1, 1], went_bad, went_good, table[0, 0], confidence)
        return Comparison(
            n=n,
            a=mean_a,
            b=mean_b,
            diff=mean_b - mean_a,
            ci_lo=lo,
            ci_hi=hi,
            p=mcnemar(went_bad, went_good),
            method="mcnemar",
            corr=_corr(a, b),
            regressed=went_bad,
            fixed=went_good,
        )
    lo, hi, p = paired_bootstrap(
        [y - x for x, y in zip(a, b)],
        clusters if grouped else None,
        replicates=replicates,
        seed=seed,
        confidence=confidence,
    )
    return Comparison(
        n=n,
        a=mean_a,
        b=mean_b,
        diff=mean_b - mean_a,
        ci_lo=lo,
        ci_hi=hi,
        p=p,
        method="bootstrap-clustered" if grouped else "bootstrap",
        corr=_corr(a, b),
        units=len(set(clusters)) if grouped else n,  # type: ignore[arg-type]
    )


# ── how many cases ───────────────────────────────────────────────────────


def _power_args(p_d: float, alpha: float, power: float) -> Tuple[float, float]:
    if not 0 < p_d <= 1:
        raise ValueError(f"the discordance rate is a share of cases in (0, 1], not {p_d!r}")
    if not 0 < alpha < 1 or not 0 < power < 1:
        raise ValueError("alpha and power are probabilities in (0, 1)")
    return z_of(1 - alpha), NormalDist().inv_cdf(power)


def paired_sample_size(
    p_d: float, delta: float, *, alpha: float = 0.05, power: float = 0.8
) -> float:
    """Cases a paired binary comparison needs to detect a drop of *delta*
    with probability *power* at two-sided level *alpha*, when a share
    *p_d* of cases differ between the two runs (McNemar's normal
    approximation, Connor 1987)::

        n ≈ (z_{1−α/2}·√p_d + z_{power}·√(p_d − δ²))² / δ²

    p_d = 0.10, δ = 0.05 → 311.6, so 312 cases. A drop is a change of
    *delta* in the discordant cases' balance, so it cannot exceed *p_d*.
    """
    za, zb = _power_args(p_d, alpha, power)
    if not 0 < delta <= p_d:
        raise ValueError(
            f"a drop of {delta!r} needs at least that share of cases to change "
            f"(discordance {p_d!r}): 0 < delta ≤ p_d"
        )
    return (za * math.sqrt(p_d) + zb * math.sqrt(p_d - delta * delta)) ** 2 / delta**2


def detectable_drop(
    n: int, p_d: float, *, alpha: float = 0.05, power: float = 0.8
) -> Optional[float]:
    """The smallest drop *n* paired cases detect with probability *power*:
    :func:`paired_sample_size` solved for δ (bisection; it falls with δ).
    ``None`` when not even a drop of *p_d* (every changed case a loss) is."""
    _power_args(p_d, alpha, power)
    if n < 1:
        raise ValueError("detectable_drop() needs at least one case")
    if paired_sample_size(p_d, p_d, alpha=alpha, power=power) > n:
        return None
    lo, hi = 1e-9, p_d
    for _ in range(100):
        mid = (lo + hi) / 2
        if paired_sample_size(p_d, mid, alpha=alpha, power=power) > n:
            lo = mid
        else:
            hi = mid
    return hi


# ── several metrics at once ──────────────────────────────────────────────


def holm(pvalues: Sequence[float]) -> List[float]:
    """Holm–Bonferroni adjusted p-values (step-down, family-wise error),
    in the input's order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    out = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        out[i] = running
    return out


def benjamini_hochberg(pvalues: Sequence[float]) -> List[float]:
    """Benjamini–Hochberg adjusted p-values (step-up, false discovery
    rate), in the input's order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i], reverse=True)
    out = [0.0] * m
    running = 1.0
    for rank_from_top, i in enumerate(order):
        rank = m - rank_from_top  # 1-based rank in ascending order
        running = min(running, pvalues[i] * m / rank)
        out[i] = min(1.0, running)
    return out
