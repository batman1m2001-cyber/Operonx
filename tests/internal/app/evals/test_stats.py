"""The statistics an eval gates on (`operonx.app.evals.stats`).

Every expected number here is computed a second way in the test — by
hand with exact fractions, by enumerating outcomes, by solving the
defining equation numerically, or by simulation against the sampling
distribution it claims to estimate — and never copied from the code's
own output.
"""

from __future__ import annotations

import itertools
import math
import random
from fractions import Fraction
from statistics import NormalDist, fmean, pstdev, stdev

import pytest

from operonx.app.evals.stats import (
    _binomial,
    _bootstrap_means,
    benjamini_hochberg,
    clustered_se,
    compare_paired,
    estimate,
    holm,
    mcnemar,
    mean_se,
    newcombe_paired,
    paired_bootstrap,
    pass_hat_k,
    seed_of,
    wilson,
    z_of,
)

Z95 = NormalDist().inv_cdf(0.975)


# ── Wilson ────────────────────────────────────────────────────────────────


def _wilson_by_bisection(k: int, n: int, z: float) -> tuple:
    """The Wilson bounds are the two p where |p̂ − p| = z·√(p(1−p)/n);
    find each root of that equation by bisection."""
    ph = k / n

    def gap(p):
        return (ph - p) ** 2 - z * z * p * (1 - p) / n

    def root(lo, hi):
        for _ in range(200):
            mid = (lo + hi) / 2
            if (gap(lo) > 0) == (gap(mid) > 0):
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    return root(0.0, ph), root(ph, 1.0)


@pytest.mark.parametrize(
    "k, n, lo, hi",
    [(45, 50, 0.786, 0.957), (43, 50, 0.738, 0.930)],  # T4 §8.1's two examples
)
def test_wilson_matches_the_published_examples_and_the_score_equation(k, n, lo, hi):
    got = wilson(k, n)
    assert got == pytest.approx((lo, hi), abs=5e-4)
    assert got == pytest.approx(_wilson_by_bisection(k, n, Z95), abs=1e-9)


def test_wilson_behaves_at_the_edges():
    assert wilson(0, 10)[0] == 0.0 and 0 < wilson(0, 10)[1] < 0.35
    assert wilson(10, 10)[1] == 1.0 and wilson(10, 10)[0] > 0.65
    assert wilson(0, 10) == pytest.approx(_wilson_by_bisection(0, 10, Z95), abs=1e-9)
    with pytest.raises(ValueError):
        wilson(3, 0)
    with pytest.raises(ValueError):
        wilson(11, 10)
    assert z_of(0.95) == pytest.approx(1.959964, abs=1e-6)


# ── McNemar ───────────────────────────────────────────────────────────────


def test_mcnemar_b8_c1_is_20_over_512():
    # by hand: 2 · P(X ≤ 1), X ~ Bin(9, ½) = 2 · (C(9,0) + C(9,1)) / 2⁹ = 20/512
    assert Fraction(2 * (1 + 9), 2**9) == Fraction(20, 512)
    assert mcnemar(8, 1) == pytest.approx(float(Fraction(20, 512)), abs=1e-12)
    assert mcnemar(8, 1) == pytest.approx(0.0391, abs=1e-4)


@pytest.mark.parametrize("b, c", [(8, 1), (1, 8), (3, 3), (0, 5), (12, 4), (0, 0)])
def test_mcnemar_equals_enumerating_every_sign_pattern(b, c):
    """Under H0 each discordant pair is a fair coin. Enumerate all 2^m
    patterns and count those at least as extreme as the observed split."""
    m = b + c
    observed = abs(b - c)
    extreme = sum(
        1 for signs in itertools.product((0, 1), repeat=m) if abs(2 * sum(signs) - m) >= observed
    )
    assert mcnemar(b, c) == pytest.approx(min(1.0, extreme / 2**m) if m else 1.0, abs=1e-12)


# ── Newcombe's paired interval ────────────────────────────────────────────


def test_newcombe_by_hand():
    # no discordant case: φ = 0 and each side is the gap to a Wilson bound,
    # so the CI is ±(1 − Wilson_lo(n, n)) — ±0.5615 for three cases
    gap = 1 - wilson(3, 3)[0]
    assert newcombe_paired(3, 0, 0, 0) == pytest.approx((-gap, gap), abs=1e-12)
    assert gap == pytest.approx(0.5615, abs=1e-4)
    # 38 both, 2 pass → fail, n = 40: A = 1.0 [l1, 1], B = 0.95 [l2, u2], φ = 0
    l1 = wilson(40, 40)[0]
    l2, u2 = wilson(38, 40)
    lo = -0.05 - math.hypot(0.0, 0.95 - l2)
    hi = -0.05 + math.hypot(1 - l1, u2 - 0.95)
    assert newcombe_paired(38, 2, 0, 0) == pytest.approx((lo, hi), abs=1e-12)
    assert (lo, hi) == pytest.approx((-0.1650, 0.0448), abs=1e-4)
    # 20 always pass, 20 always fail: AD − BC = 400 shrinks to 380, φ = 380/400,
    # and each side is the Wilson half-width h of 20/40 times √(2 − 2φ)
    lo_w, hi_w = wilson(20, 40)
    h = 0.5 - lo_w
    assert hi_w - 0.5 == pytest.approx(h)
    side = math.sqrt(2 * h * h * (1 - 0.95))
    assert newcombe_paired(20, 0, 0, 20) == pytest.approx((-side, side), abs=1e-12)


def test_newcombe_covers_the_true_difference_95_percent_of_the_time():
    """Cases pass A with q ~ Beta(4, 1) and B with 0.9·q: the true
    difference is 0.9·E[q] − E[q] = 0.72 − 0.8 = −0.08."""
    rng = random.Random(3)
    sims, covered = 3000, 0
    for _ in range(sims):
        t = {(1, 1): 0, (1, 0): 0, (0, 1): 0, (0, 0): 0}
        for _ in range(60):
            q = rng.random() ** 0.25  # Beta(4, 1)
            t[int(rng.random() < q), int(rng.random() < 0.9 * q)] += 1
        lo, hi = newcombe_paired(t[1, 1], t[1, 0], t[0, 1], t[0, 0])
        covered += lo <= -0.08 <= hi
    assert 0.925 <= covered / sims <= 0.97


def test_newcombe_covers_when_most_cases_always_pass_or_always_fail():
    """Half the cases pass with q = 1, half with q = 0.02; B passes with
    0.97·q, a 1.5-point drop. The runs agree on almost every case, which is
    where an uncorrected correlation made the interval far too narrow."""
    rng = random.Random(5)
    true = 0.5 * (0.97 - 1) + 0.5 * 0.02 * (0.97 - 1)
    sims, covered = 2000, 0
    for _ in range(sims):
        t = {(1, 1): 0, (1, 0): 0, (0, 1): 0, (0, 0): 0}
        for _ in range(40):
            q = 1.0 if rng.random() < 0.5 else 0.02
            t[int(rng.random() < q), int(rng.random() < 0.97 * q)] += 1
        lo, hi = newcombe_paired(t[1, 1], t[1, 0], t[0, 1], t[0, 0])
        covered += lo <= true <= hi
    assert covered / sims >= 0.95


# ── pass^k ────────────────────────────────────────────────────────────────


def test_pass_hat_3_with_4_of_5_is_0_4():
    trials = [1, 1, 1, 1, 0]
    subsets = list(itertools.combinations(trials, 3))
    all_pass = sum(1 for s in subsets if all(s)) / len(subsets)
    assert all_pass == 0.4
    assert pass_hat_k(4, 5, 3) == pytest.approx(all_pass)
    assert pass_hat_k(4, 5, 1) == pytest.approx(0.8)  # pass^1 is the pass rate
    assert pass_hat_k(5, 5, 5) == 1.0 and pass_hat_k(4, 5, 5) == 0.0
    with pytest.raises(ValueError):
        pass_hat_k(2, 3, 4)


# ── standard errors ───────────────────────────────────────────────────────


def test_clustered_se_by_hand():
    # s̄ = 0.5; cluster sums of deviations: A = +1, B = −1 → √2 / 4
    values, clusters = [1, 1, 0, 0], ["A", "A", "B", "B"]
    assert clustered_se(values, clusters) == pytest.approx(math.sqrt(2) / 4)
    # one case per cluster: √(Σ(s−s̄)²)/n = the population SD / √n
    assert clustered_se(values, list("abcd")) == pytest.approx(pstdev(values) / 2)
    assert mean_se(values)[1] == pytest.approx(stdev(values) / 2)
    assert clustered_se(values, clusters) > mean_se(values)[1]


def _cluster_sample(rng, clusters=40, size=5):
    """Cases in a cluster share a pass probability of 0.1 or 0.9."""
    values, ids = [], []
    for c in range(clusters):
        p = 0.9 if rng.random() < 0.5 else 0.1
        for _ in range(size):
            values.append(1.0 if rng.random() < p else 0.0)
            ids.append(c)
    return values, ids


def test_clustered_se_tracks_the_true_spread_and_the_naive_one_does_not():
    rng = random.Random(7)
    means, clustered, naive = [], [], []
    for _ in range(600):
        values, ids = _cluster_sample(rng)
        means.append(fmean(values))
        clustered.append(clustered_se(values, ids))
        naive.append(mean_se(values)[1])
    true_sd = stdev(means)  # the spread of the mean across repeated datasets
    assert fmean(clustered) == pytest.approx(true_sd, rel=0.12)
    assert fmean(naive) < 0.6 * true_sd  # under intra-cluster correlation, far too small
    assert all(c >= n for c, n in zip(clustered, naive))


def test_estimate_picks_its_method():
    binary = estimate([1, 1, 0, 1])
    assert binary.method == "wilson" and (binary.ci_lo, binary.ci_hi) == wilson(3, 4)
    shares = estimate([1, 2 / 3, 1 / 3, 1], bounds=(0, 1))
    m, se = mean_se([1, 2 / 3, 1 / 3, 1])
    assert shares.method == "clt" and shares.se == pytest.approx(se)
    assert shares.ci_hi == pytest.approx(min(1, m + Z95 * se))
    grouped = estimate([1, 1, 0, 0], ["A", "A", "B", "B"])
    assert grouped.method == "clustered" and grouped.se == pytest.approx(math.sqrt(2) / 4)
    assert estimate([0.5]).se is None and estimate([0.5]).ci_lo is None


# ── the bootstrap ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("n, p", [(50, 0.3), (300, 0.07), (7, 0.9), (4000, 0.5)])
def test_the_binomial_sampler_has_the_binomial_moments_and_pmf(n, p):
    rng = random.Random(seed_of("binomial", n, p))
    draws = [_binomial(n, p, rng) for _ in range(20000)]
    assert all(0 <= d <= n for d in draws)
    assert fmean(draws) == pytest.approx(n * p, abs=4 * math.sqrt(n * p * (1 - p) / 20000))
    assert pstdev(draws) ** 2 == pytest.approx(n * p * (1 - p), rel=0.05)
    if n <= 50:  # every outcome's frequency against the exact pmf
        for k in range(n + 1):
            pk = math.comb(n, k) * p**k * (1 - p) ** (n - k)
            freq = draws.count(k) / len(draws)
            assert abs(freq - pk) < 5 * math.sqrt(pk * (1 - pk) / len(draws)) + 1e-4


def test_the_binomial_sampler_edges():
    rng = random.Random(1)
    assert _binomial(0, 0.5, rng) == 0 and _binomial(9, 0.0, rng) == 0
    assert _binomial(9, 1.0, rng) == 9


def test_resampling_categories_matches_resampling_cases():
    """The bootstrap draws one multinomial over distinct values instead of
    n cases. Its replicates must have the distribution of the plain
    case-by-case bootstrap (random.choices)."""
    rng = random.Random(3)
    diffs = [rng.choice([-1, 0, 0, 0, 0, 1]) for _ in range(120)]
    fast = _bootstrap_means([(float(d), 1) for d in diffs], 20000, random.Random(11))
    slow = sorted(fmean(rng.choices(diffs, k=len(diffs))) for _ in range(20000))
    assert fmean(fast) == pytest.approx(fmean(slow), abs=0.002)
    assert pstdev(fast) == pytest.approx(pstdev(slow), rel=0.03)
    for q in (0.025, 0.5, 0.975):
        i = int(q * 20000)
        assert fast[i] == pytest.approx(slow[i], abs=0.0085)  # one step is 1/120


def test_the_bootstrap_se_is_the_analytic_se():
    rng = random.Random(5)
    diffs = [rng.gauss(0.1, 0.4) for _ in range(200)]
    reps = _bootstrap_means([(d, 1) for d in diffs], 4000, random.Random(2))
    # the bootstrap distribution of a mean has SD = population SD / √n
    assert pstdev(reps) == pytest.approx(pstdev(diffs) / math.sqrt(200), rel=0.05)


def test_the_seeded_bootstrap_is_deterministic():
    rng = random.Random(9)
    diffs = [rng.choice([-1.0, -1 / 3, 0.0, 1 / 3, 2 / 3]) for _ in range(80)]
    one = paired_bootstrap(diffs, seed=seed_of("a", "b"))
    assert paired_bootstrap(diffs, seed=seed_of("a", "b")) == one
    # input order does not change the draws: units are sorted first
    assert paired_bootstrap(list(reversed(diffs)), seed=seed_of("a", "b")) == one
    assert paired_bootstrap(diffs, seed=seed_of("a", "c")) != one
    assert seed_of("x", 1) == seed_of("x", 1) != seed_of("x", 2)


def test_bootstrap_p_and_ci_agree_and_clusters_widen_it():
    rng = random.Random(4)
    diffs = [rng.choice([-1, 0, 0, 0, 1]) - 0.15 for _ in range(150)]
    lo, hi, p = paired_bootstrap(diffs, seed=1)
    assert (hi < 0 or lo > 0) == (p < 0.05)
    clusters = [i // 10 for i in range(150)]  # 15 groups of ten
    grouped = [d + (0.6 if c % 2 else -0.6) for d, c in zip(diffs, clusters)]
    lo_c, hi_c, _ = paired_bootstrap(grouped, clusters, seed=1)
    lo_i, hi_i, _ = paired_bootstrap(grouped, seed=1)
    assert hi_c - lo_c > 1.5 * (hi_i - lo_i)  # whole clusters move together
    assert paired_bootstrap([0.0] * 30, seed=1) == (0.0, 0.0, 1.0)


def test_compare_paired_picks_mcnemar_for_binary_checks():
    a = [1] * 30 + [0] * 10 + [1] * 8 + [0] * 1
    b = [1] * 30 + [0] * 10 + [0] * 8 + [1] * 1
    got = compare_paired(a, b, seed=1)
    assert got.method == "mcnemar" and (got.regressed, got.fixed) == (8, 1)
    assert got.p == pytest.approx(20 / 512) and got.diff == pytest.approx(-7 / 49)
    assert (got.ci_lo, got.ci_hi) == newcombe_paired(30, 8, 1, 10) and got.ci_hi < 0
    assert got.units is None
    shares = compare_paired([1, 2 / 3, 1, 1 / 3], [2 / 3, 2 / 3, 1, 0], seed=1)
    assert shares.method == "bootstrap" and shares.regressed is None and shares.units == 4
    grouped = compare_paired(a, b, ["g"] * 2 + list(range(47)), seed=1)
    assert grouped.method == "bootstrap-clustered" and grouped.units == 48


# ── several metrics ───────────────────────────────────────────────────────


def test_holm_by_hand():
    # sorted: .005·4 = .02, .01·3 = .03, .03·2 = .06, .04·1 = .04 → running max .06
    assert holm([0.01, 0.04, 0.03, 0.005]) == pytest.approx([0.03, 0.06, 0.06, 0.02])
    assert holm([0.2]) == [0.2] and holm([0.6, 0.7]) == [1.0, 1.0]


def test_benjamini_hochberg_by_hand():
    # ranks 1..4: .005·4/1 = .02, .01·4/2 = .02, .03·4/3 = .04, .04·4/4 = .04
    assert benjamini_hochberg([0.01, 0.04, 0.03, 0.005]) == pytest.approx([0.02, 0.04, 0.04, 0.02])
    # step-up: a large p further down pulls nothing up
    assert benjamini_hochberg([0.01, 0.02, 0.9]) == pytest.approx([0.03, 0.03, 0.9])
