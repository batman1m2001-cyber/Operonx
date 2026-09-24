"""Per-call cost on `LLMOp`.

`cost_per_input_token` and `cost_per_output_token` have been in
`operonx/providers/llms/config.py` since the port, read by nothing — a
resource could set them and no number came out anywhere. hush computed the
same product in its `LLMOp` and wrote it to state; the config crossed over
and the body did not.

The distinction these tests defend hardest is **None vs 0.0**. An unpriced
resource has an *unknown* cost. Returning `0.0` would sum into a batch total
as though the call were free, which is the same class of bug as an empty
retrieval pool reading as "no violation": absent quietly becoming a value.
"""

import pytest

from operonx.providers.ops import LLMOp


class _Config:
    """Stand-in for an LLM resource config carrying prices."""

    def __init__(self, cost_in=None, cost_out=None):
        self.cost_per_input_token = cost_in
        self.cost_per_output_token = cost_out


class _LLM:
    def __init__(self, config):
        self.config = config


def _op(resource="primary", config=None, fallback=None, fallback_configs=()):
    op = LLMOp(name="cost_test", resource=resource, fallback=list(fallback or []) or None)
    op._llms = [_LLM(config)] if config is not None else []
    op._fallback_llms = [_LLM(c) for c in fallback_configs]
    return op


USAGE = {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}


# ── the number itself ──────────────────────────────────────────────────


class TestComputation:
    def test_both_prices(self):
        op = _op(config=_Config(cost_in=0.000002, cost_out=0.000008))
        # 1000 * 2e-6 + 200 * 8e-6
        assert op._cost_usd("primary", USAGE) == pytest.approx(0.0036)

    def test_only_input_priced(self):
        op = _op(config=_Config(cost_in=0.000002))
        assert op._cost_usd("primary", USAGE) == pytest.approx(0.002)

    def test_only_output_priced(self):
        op = _op(config=_Config(cost_out=0.000008))
        assert op._cost_usd("primary", USAGE) == pytest.approx(0.0016)

    def test_zero_tokens_on_a_priced_resource_is_zero_not_none(self):
        """Priced and nothing spent is a measurement: 0.0 is the answer."""
        op = _op(config=_Config(cost_in=0.000002, cost_out=0.000008))
        got = op._cost_usd("primary", {"prompt_tokens": 0, "completion_tokens": 0})
        assert got == 0.0
        assert got is not None

    def test_missing_usage_keys_count_as_zero(self):
        op = _op(config=_Config(cost_in=0.000002, cost_out=0.000008))
        assert op._cost_usd("primary", {}) == 0.0

    def test_none_token_counts_do_not_raise(self):
        """A provider that reports nulls should not take the call down."""
        op = _op(config=_Config(cost_in=0.000002, cost_out=0.000008))
        assert op._cost_usd("primary", {"prompt_tokens": None, "completion_tokens": None}) == 0.0


# ── absent is not zero ─────────────────────────────────────────────────


class TestUnpriced:
    def test_no_prices_gives_none(self):
        op = _op(config=_Config())
        assert op._cost_usd("primary", USAGE) is None

    def test_no_llm_resolved_gives_none(self):
        op = _op(config=None)
        assert op._cost_usd("primary", USAGE) is None

    def test_a_resource_key_we_never_resolved_gives_none(self):
        op = _op(config=_Config(cost_in=0.000002))
        assert op._cost_usd("some-other-key", USAGE) is None

    def test_none_is_distinguishable_from_free(self):
        """The whole point — a batch summing costs must be able to tell
        'we did not measure this call' from 'this call was free'."""
        unpriced = _op(config=_Config())._cost_usd("primary", USAGE)
        free = _op(config=_Config(cost_in=0.0, cost_out=0.0))._cost_usd("primary", USAGE)
        assert unpriced is None
        assert free == 0.0


# ── which resource actually served the call ────────────────────────────


class TestResourceSelection:
    def test_a_list_resource_prices_by_the_key_that_served(self):
        op = LLMOp(name="cost_list", resource=["cheap", "dear"])
        op._llms = [_LLM(_Config(cost_in=0.000001)), _LLM(_Config(cost_in=0.00001))]
        assert op._cost_usd("cheap", USAGE) == pytest.approx(0.001)
        assert op._cost_usd("dear", USAGE) == pytest.approx(0.01)

    def test_a_fallback_is_priced_at_the_fallback_rate(self):
        """A call that fell back reports the fallback's key; pricing it at
        the primary's rate would quietly misreport every degraded call."""
        op = _op(
            config=_Config(cost_in=0.000001),
            fallback=["backup"],
            fallback_configs=[_Config(cost_in=0.00005)],
        )
        assert op._cost_usd("primary", USAGE) == pytest.approx(0.001)
        assert op._cost_usd("backup", USAGE) == pytest.approx(0.05)

    def test_llm_for_resource_finds_primary_and_fallback(self):
        op = _op(
            config=_Config(cost_in=1.0),
            fallback=["backup"],
            fallback_configs=[_Config(cost_in=2.0)],
        )
        assert op._llm_for_resource("primary").config.cost_per_input_token == 1.0
        assert op._llm_for_resource("backup").config.cost_per_input_token == 2.0
        assert op._llm_for_resource("ghost") is None


# ── it reaches the outputs ─────────────────────────────────────────────


class TestOutputSchema:
    def test_cost_usd_is_declared(self, hub):
        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")
        op = LLMOp(name="cost_schema", resource="gpt-4o")
        assert "cost_usd" in op.outputs

    def test_cost_usd_defaults_to_none(self, hub):
        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")
        op = LLMOp(name="cost_default", resource="gpt-4o")
        assert op.outputs["cost_usd"].default is None


# ── documented approximation ───────────────────────────────────────────


class TestCachedTokens:
    def test_cached_prompt_tokens_bill_at_the_input_rate(self):
        """`prompt_tokens` already includes `cached_tokens`, and operonx has
        no cached-rate field — so a cache-heavy call reads high rather than
        silently guessing a discount. `usage` keeps `cached_tokens` so a
        consumer that knows its cache price can recompute.
        """
        op = _op(config=_Config(cost_in=0.000002))
        usage = {"prompt_tokens": 1000, "completion_tokens": 0, "cached_tokens": 900}
        assert op._cost_usd("primary", usage) == pytest.approx(0.002)


# ── a cost annotation must never fail the call ─────────────────────────


class TestNeverFatal:
    """`_cost_usd` runs inside `_extract_completion`, on the path of every
    LLM response. It annotates; it does not decide anything. So every way it
    can go wrong has to end in `None`, not an exception — otherwise a
    mispriced config takes down scoring.

    This is not hypothetical: adding the lookup broke 17 existing tests at
    once, because they build a partial `LLMOp` and `resource` is an unset
    `__slots__` attribute, which raises on access rather than returning None.
    """

    def test_an_op_with_no_resource_slot_set(self):
        op = LLMOp.__new__(LLMOp)
        assert op._cost_usd("anything", USAGE) is None

    def test_a_config_whose_price_is_not_a_number(self):
        op = _op(config=_Config(cost_in="free"))
        assert op._cost_usd("primary", USAGE) is None

    def test_usage_that_is_not_a_mapping(self):
        op = _op(config=_Config(cost_in=0.000002))
        assert op._cost_usd("primary", None) == 0.0

    def test_a_config_that_raises_on_attribute_access(self):
        class _Hostile:
            def __getattr__(self, name):
                raise RuntimeError("boom")

        op = _op(config=None)
        op._llms = [_LLM(_Hostile())]
        assert op._cost_usd("primary", USAGE) is None
