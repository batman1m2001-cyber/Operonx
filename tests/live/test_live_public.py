"""Live checks that need the open internet — **VPN off**.

The corporate VPN and the public internet are mutually exclusive on this
machine, so the live suite is split by network zone rather than by
feature. This half runs against OpenRouter and OpenAI.

That split is not a compromise. Most of what 1.6.0 added is
provider-agnostic — the transport retry, the ``generation_extras`` merge
with its null-strips-key rule, callable validators, the LLMOp path that
strings them together. None of it knows which gateway is on the far end,
so all of it can be proven on a public one. What genuinely cannot be is
in ``test_live_private.py``: fetching an OAuth2 token from a specific
issuer, and the message shaping two specific proxies demand.

Run:

    OPERONX_LIVE=1 pytest tests/live -m public -v
"""

from __future__ import annotations

import numpy as np
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.public]


def _user(text):
    return [{"role": "user", "content": text}]


PUBLIC_LLMS = ["llm:or-claude-4-sonnet", "llm:gpt-4o"]


class TestTransportRetry:
    """OpenRouter *is* a shared gateway — it rate-limits, which is the
    whole reason resource-level retry exists."""

    async def test_policy_reaches_the_client(self, require_key):
        llm = require_key("llm:or-claude-4-sonnet")
        assert llm.config.max_retries >= 1
        assert llm.config.retry_base_delay > 0
        assert llm.config.retry_on_empty is True

    async def test_call_through_the_retry_wrapper(self, require_key):
        """The path every graph takes, against a real endpoint."""
        from operonx.providers.ops.llm import LLMOp

        llm = require_key("llm:or-claude-4-sonnet")
        op = LLMOp.__new__(LLMOp)
        op.name = "live_probe"
        out = await op._call_with_retry(
            llm.generate,
            llm=llm,
            messages=_user("Reply with exactly: OK"),
            max_tokens=16,
        )
        assert out.choices[0].message.content is not None

    async def test_a_4xx_is_not_retried(self, require_key):
        """Retrying a bad request only burns quota. Asking for a model
        that does not exist is the cheapest way to prove 4xx propagates
        instead of sleeping through five backoffs."""
        import time

        import openai

        from operonx.providers.ops.llm import LLMOp

        llm = require_key("llm:or-claude-4-sonnet")
        op = LLMOp.__new__(LLMOp)
        op.name = "live_probe"

        original_model = llm.config.model
        llm.config.model = "nonexistent/model-that-cannot-resolve"
        started = time.monotonic()
        try:
            with pytest.raises(openai.APIStatusError) as exc:
                await op._call_with_retry(llm.generate, llm=llm, messages=_user("hi"), max_tokens=8)
            assert 400 <= exc.value.status_code < 500
            # Five retries at retry_base_delay 5.0 would take far longer.
            assert time.monotonic() - started < 20
        finally:
            llm.config.model = original_model


class TestGenerationExtras:
    """`response_format` on gpt-4o is a vendor knob with no first-class
    field — the same mechanism the private gateways use for
    `reasoning_effort` and `thinking`."""

    async def test_declared_on_the_resource(self, require_key):
        llm = require_key("llm:gpt-4o-json")
        extras = llm.config.generation_extras or {}
        assert extras.get("response_format") == {"type": "json_object"}

    async def test_merged_into_the_call_and_honoured(self, require_key):
        import json

        from operonx.providers.ops.llm import LLMOp

        llm = require_key("llm:gpt-4o-json")
        merged = LLMOp._merge_generation_extras(
            llm, {"messages": _user('Reply in JSON with {"colour": "<one word>"}.')}
        )
        assert merged["response_format"] == {"type": "json_object"}

        out = await llm.generate(
            _user('Reply in JSON with {"colour": "<one word>"} for the clear sky.'),
            response_format=merged["response_format"],
            max_tokens=32,
        )
        content = out.choices[0].message.content
        # json_object mode is what makes this parse rather than hedge.
        assert isinstance(json.loads(content), dict)

    async def test_null_strips_the_key(self, require_key):
        """A resource opts out of something operonx would otherwise send
        by declaring it null — the key must vanish, not arrive as null."""
        from operonx.providers.ops.llm import LLMOp

        llm = require_key("llm:or-claude-4-sonnet")
        merged = LLMOp._merge_generation_extras(llm, {"messages": _user("hi"), "top_p": None})
        params = llm._prepare_params(
            model=llm.config.model,
            messages=_user("hi"),
            stream=False,
            temperature=0.0,
            top_p=merged.get("top_p"),
        )
        assert "top_p" not in params
        assert params["temperature"] == 0.0

        out = await llm.generate(_user("Reply with exactly: OK"), top_p=None, max_tokens=16)
        assert out.choices[0].message.content is not None


class TestCallableValidator:
    @pytest.mark.parametrize("resource", PUBLIC_LLMS)
    async def test_structured_output_end_to_end(self, live_hub, require_key, resource):
        """hub -> backend -> transport retry -> parse -> callable validator."""
        from operonx.core.registry import ResourceHub
        from operonx.providers.ops.llm import LLMOp

        require_key(resource)
        ResourceHub.set_instance(live_hub)

        def shape_ok(parsed: dict) -> bool:
            return isinstance(parsed.get("answer"), str) and bool(parsed["answer"])

        op = LLMOp.of(
            resource=resource.split(":", 1)[1],
            fields=["answer: str"],
            parser="json",
            validators=shape_ok,
            max_retries=1,
            name="live_probe",
        )
        op._ensure_initialized()
        result = await op._structured_generate(
            {
                "messages": [
                    {
                        "role": "system",
                        "content": 'Reply ONLY with JSON: {"answer": "<one word>"}',
                    },
                    {"role": "user", "content": "What colour is the sky on a clear day?"},
                ],
                "max_tokens": 64,
            }
        )
        assert result.get("error") is None, result.get("error")
        assert isinstance(result["answer"], str) and result["answer"]

    async def test_a_rejecting_validator_surfaces_an_error(self, live_hub, require_key):
        """The other half of the contract: a predicate that never passes
        must exhaust the semantic retries and report, not hang or crash."""
        from operonx.core.registry import ResourceHub
        from operonx.providers.ops.llm import LLMOp

        require_key("llm:gpt-4o")
        ResourceHub.set_instance(live_hub)

        op = LLMOp.of(
            resource="gpt-4o",
            fields=["answer: str"],
            parser="json",
            validators=lambda parsed: False,
            max_retries=0,
            name="live_probe_reject",
        )
        op._ensure_initialized()
        result = await op._structured_generate(
            {
                "messages": [
                    {"role": "system", "content": 'Reply ONLY with JSON: {"answer": "x"}'},
                    {"role": "user", "content": "Say x."},
                ],
                "max_tokens": 32,
            }
        )
        assert result.get("error") is not None
        assert "rejected the parsed output" in result["error"]


class TestResourceHubAlias:
    async def test_alias_routes_a_real_call(self, live_hub, require_key):
        """The C6-friendly indirection, end to end: the call site names a
        role, the hub decides which resource fills it."""
        require_key("llm:gpt-4o")
        live_hub.alias("llm:scanner", "llm:gpt-4o")
        try:
            llm = live_hub.get("llm:scanner")
            assert llm.config.model == "gpt-4o"
            out = await llm.generate(_user("Reply with exactly: OK"), max_tokens=16)
            assert out.choices[0].message.content is not None
        finally:
            live_hub.unalias("llm:scanner")


class TestEmbeddingSanity:
    """Not a 1.6.0 feature, but the assertions the Triton tests make —
    normalisation, ordering, batch stability — are worth exercising on a
    reachable backend so a failure over VPN can be attributed."""

    async def test_vectors_are_normalised_and_ordered(self, require_key):
        emb = require_key("embedding:openai")
        a, b = "the customer complained", "the agent said hello"
        both = (await emb.run([a, b]))["embeddings"]
        first = (await emb.run([a]))["embeddings"][0]
        assert len(both) == 2
        assert np.allclose(both[0], first, atol=1e-4)

    async def test_semantics_are_sane(self, require_key):
        emb = require_key("embedding:openai")
        vecs = (
            await emb.run(["please pay the bill", "kindly settle the payment", "it is raining"])
        )["embeddings"]
        v = [np.array(x) / np.linalg.norm(x) for x in vecs]
        near, far = float(v[0] @ v[1]), float(v[0] @ v[2])
        assert near > far, f"near={near:.4f} not above far={far:.4f}"
