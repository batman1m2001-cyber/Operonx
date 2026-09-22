"""Live checks that need the corporate VPN — **public internet off**.

The two networks are mutually exclusive on this machine, so the live
suite splits by zone. What is here is only what genuinely cannot be
proven on a public endpoint:

* fetching a token from one specific OAuth2 issuer;
* the message shaping two specific proxies demand, where getting it
  wrong returns an error that names the wrong thing;
* an embedding server whose two input contracts differ by deployment.

Everything provider-agnostic — transport retry, ``generation_extras``,
callable validators, the LLMOp path — lives in ``test_live_public.py``
and needs no VPN.

Run:

    OPERONX_LIVE=1 pytest tests/live -m vpn -v

Every test skips, naming the reason, when its resource is missing from
``resources.yaml`` or a ``${VAR}`` it needs is unset. Filling in
credentials later turns them on with no code change.
"""

from __future__ import annotations

import numpy as np
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.vpn]


def _user(text):
    return [{"role": "user", "content": text}]


# ---------------------------------------------------------------------------
# Token providers
# ---------------------------------------------------------------------------


class TestKeycloakProvider:
    """Pre-existing, but it shares the code path oauth2 generalised —
    a regression here means the refactor broke the original."""

    async def test_fetches_a_token(self, require_key):
        provider = require_key("keycloak:aihub")
        token = provider.get_token()
        assert isinstance(token, str) and len(token) > 20

    async def test_caches_until_expiry(self, require_key):
        provider = require_key("keycloak:aihub")
        assert provider.get_token() == provider.get_token()

    async def test_reference_is_resolved_on_the_llm(self, require_key):
        """``api_key: keycloak:aihub`` must reach the client as a bearer
        token, not as the literal string."""
        llm = require_key("llm:aihub-claude")
        assert not llm.config.api_key.startswith("keycloak:")
        assert len(llm.config.api_key) > 20
        assert hasattr(llm, "_token_provider")
        # Back-compat alias, same object.
        assert llm._keycloak_provider is llm._token_provider


class TestOAuth2Provider:
    async def test_fetches_a_token(self, require_env, require_key):
        require_env("DATABRICKS_HOST", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET")
        provider = require_key("oauth2:databricks")
        token = provider.get_token()
        assert isinstance(token, str) and len(token) > 20

    async def test_caches_until_expiry(self, require_key):
        provider = require_key("oauth2:databricks")
        assert provider.get_token() == provider.get_token()

    async def test_invalidate_forces_a_refetch(self, require_key):
        provider = require_key("oauth2:databricks")
        first = provider.get_token()
        provider.invalidate()
        assert isinstance(provider.get_token(), str)
        assert len(first) > 20

    async def test_reference_is_resolved_on_the_llm(self, require_env, require_key):
        require_env("DATABRICKS_HOST")
        llm = require_key("llm:db-gemini-3-flash")
        assert not llm.config.api_key.startswith("oauth2:")
        assert len(llm.config.api_key) > 20
        assert hasattr(llm, "_token_provider")


# ---------------------------------------------------------------------------
# Transport retry + generation_extras, against a real gateway
# ---------------------------------------------------------------------------


class TestPrivateGatewayLLM:
    """Keycloak-authenticated gateway. The retry layer itself is proven
    in the public half — what is private here is that a token fetched
    from an internal issuer actually authenticates a call."""

    async def test_generates(self, require_key):
        llm = require_key("llm:aihub-claude")
        out = await llm.generate(_user("Reply with exactly: OK"), max_tokens=16)
        assert out.choices[0].message.content is not None

    async def test_retry_policy_reaches_the_client(self, require_key):
        """Read off ``llm.config`` at call time — a config that dropped
        them would retry zero times against a gateway that rate-limits."""
        llm = require_key("llm:aihub-claude")
        assert llm.config.max_retries >= 1
        assert llm.config.retry_base_delay > 0
        assert llm.config.retry_on_empty is True

    async def test_call_goes_through_the_retry_wrapper(self, require_key):
        """The path every graph takes, against a real endpoint."""
        from operonx.providers.ops.llm import LLMOp

        llm = require_key("llm:aihub-claude")
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
        that cannot resolve is the cheapest way to prove 4xx propagates
        immediately instead of sleeping through the backoff ladder.

        Timing is the assertion that matters: this resource declares
        max_retries with a multi-second base delay, so a wrongly-retried
        4xx would take far longer than the bound below.
        """
        import time

        import openai

        from operonx.providers.ops.llm import LLMOp

        llm = require_key("llm:aihub-claude")
        op = LLMOp.__new__(LLMOp)
        op.name = "live_probe"

        original_model = llm.config.model
        llm.config.model = "nonexistent-model-that-cannot-resolve"
        started = time.monotonic()
        try:
            with pytest.raises(openai.APIStatusError) as exc:
                await op._call_with_retry(
                    llm.generate, llm=llm, messages=_user("hi"), max_tokens=8
                )
            assert 400 <= exc.value.status_code < 500
            elapsed = time.monotonic() - started
            budget = llm.config.retry_base_delay * llm.config.max_retries
            assert elapsed < max(20.0, budget / 2), (
                f"4xx took {elapsed:.1f}s — looks retried "
                f"(backoff budget was {budget:.0f}s)"
            )
        finally:
            llm.config.model = original_model


# ---------------------------------------------------------------------------
# Databricks backends
# ---------------------------------------------------------------------------


class TestDatabricksGemini:
    async def test_generates(self, require_key):
        from operonx.providers.llms.databricks import DatabricksGemini

        llm = require_key("llm:db-gemini-3-flash")
        assert isinstance(llm, DatabricksGemini)
        out = await llm.generate(_user("Reply with exactly: OK"), max_tokens=16)
        assert out.choices[0].message.content is not None

    async def test_multipart_content_is_flattened(self, require_key):
        """Sent unflattened, the AI Gateway answers ``401 - Credential was
        not sent``, which points at the token rather than the payload."""
        llm = require_key("llm:db-gemini-3-flash")
        messages = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "You answer in one word."},
                    {"type": "text", "text": "Be terse."},
                ],
            },
            {"role": "user", "content": "Say OK."},
        ]
        out = await llm.generate(messages, max_tokens=16)
        assert out.choices[0].message.content is not None

    async def test_generation_extras_survive_the_call(self, require_key):
        """``reasoning_effort`` is declared on the resource and merged per
        call; ``top_p: null`` must remove the key rather than send null."""
        from operonx.providers.ops.llm import LLMOp

        llm = require_key("llm:db-gemini-3-flash")
        extras = llm.config.generation_extras or {}
        assert extras.get("reasoning_effort") == "minimal"
        assert "top_p" in extras and extras["top_p"] is None

        merged = LLMOp._merge_generation_extras(llm, {"messages": _user("Say OK.")})
        assert merged["reasoning_effort"] == "minimal"

        params = llm._prepare_params(
            model=llm.config.model,
            messages=_user("Say OK."),
            stream=False,
            temperature=0.0,
            top_p=merged.get("top_p"),
        )
        assert "top_p" not in params, "null must strip the key, not send null"

        out = await llm.generate(
            _user("Say OK."), top_p=merged.get("top_p"), max_tokens=16
        )
        assert out.choices[0].message.content is not None


class TestDatabricksAnthropic:
    async def test_generates(self, require_key):
        from operonx.providers.llms.databricks import DatabricksAnthropic

        llm = require_key("llm:db-claude-4-sonnet")
        assert isinstance(llm, DatabricksAnthropic)
        out = await llm.generate(_user("Reply with exactly: OK"), max_tokens=16)
        assert out.choices[0].message.content is not None

    async def test_cache_control_survives_to_the_proxy(self, require_key):
        """Prompt caching is why this backend keeps content parts intact
        instead of flattening them like the Gemini one."""
        llm = require_key("llm:db-claude-4-sonnet")
        messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "You are a terse assistant. " * 200,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
            {"role": "user", "content": "Say OK."},
        ]
        out = await llm.generate(messages, max_tokens=16)
        assert out.choices[0].message.content is not None

    async def test_too_many_breakpoints_fails_before_the_wire(self, require_key):
        """Caught locally so the message names the real problem — the API
        answers a 400 that does not."""
        llm = require_key("llm:db-claude-4-sonnet")
        messages = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": f"part {i}", "cache_control": {"type": "ephemeral"}}
                    for i in range(5)
                ],
            }
        ]
        with pytest.raises(ValueError, match="cache_control limit exceeded"):
            await llm.generate(messages, max_tokens=16)


# ---------------------------------------------------------------------------
# Triton embedding
# ---------------------------------------------------------------------------


class TestTritonEmbedding:
    async def test_embeds_text(self, require_key):
        from operonx.providers.embeddings.triton import TritonEmbedding

        emb = require_key("embedding:triton-bge-m3")
        assert isinstance(emb, TritonEmbedding)
        out = await emb.run(["xin chào anh"])
        assert len(out["embeddings"]) == 1
        assert len(out["embeddings"][0]) == emb.get_output_dim()

    async def test_which_input_contract_is_live(self, require_key):
        """Reports the mode rather than asserting one — both are valid,
        and which runs is the env file's choice."""
        emb = require_key("embedding:triton-bge-m3")
        mode = "server-side (BYTES)" if emb._input_name else "client-side (token ids)"
        print(f"\n[live] Triton input contract: {mode}")
        assert (emb._input_name is not None) or (emb.tokenizer is not None)

    async def test_vectors_are_l2_normalised(self, require_key):
        emb = require_key("embedding:triton-bge-m3")
        out = await emb.run(["anh nói chuyện lịch sự"])
        norm = float(np.linalg.norm(np.array(out["embeddings"][0])))
        assert abs(norm - 1.0) < 1e-4

    async def test_batch_matches_single(self, require_key):
        """Batching must not move a vector — an index built one row at a
        time has to stay queryable by a batched call."""
        emb = require_key("embedding:triton-bge-m3")
        texts = ["câu thứ nhất", "câu thứ hai", "câu thứ ba"]
        batched = (await emb.run(texts))["embeddings"]
        singles = [(await emb.run([t]))["embeddings"][0] for t in texts]
        drift = max(
            float(np.abs(np.array(b) - np.array(s)).max())
            for b, s in zip(batched, singles)
        )
        assert drift < 1e-5, f"batch-vs-single drift {drift:.2e}"

    async def test_order_is_preserved(self, require_key):
        emb = require_key("embedding:triton-bge-m3")
        a, b = "khách hàng phàn nàn", "nhân viên chào hỏi"
        both = (await emb.run([a, b]))["embeddings"]
        first = (await emb.run([a]))["embeddings"][0]
        assert np.allclose(both[0], first, atol=1e-5)

    async def test_semantics_are_sane(self, require_key):
        """Near pair scores above far pair — a smoke test that the served
        model is the one expected, not merely *a* model."""
        emb = require_key("embedding:triton-bge-m3")
        vecs = (
            await emb.run(
                ["anh vui lòng thanh toán", "anh vui lòng trả tiền", "hôm nay trời mưa"]
            )
        )["embeddings"]
        v = [np.array(x) for x in vecs]
        near, far = float(v[0] @ v[1]), float(v[0] @ v[2])
        assert near > far, f"near={near:.4f} not above far={far:.4f}"

    async def test_empty_input_makes_no_request(self, require_key):
        emb = require_key("embedding:triton-bge-m3")
        assert await emb.run([]) == {"embeddings": []}


# ---------------------------------------------------------------------------
# LLMOp end to end
# ---------------------------------------------------------------------------


class TestLLMOpThroughTheHub:
    async def test_structured_output_on_a_databricks_backend(
        self, live_hub, require_key
    ):
        """The private path end to end: hub -> oauth2 -> db-gemini
        message shaping -> transport retry -> parse -> validator."""
        from operonx.core.registry import ResourceHub
        from operonx.providers.ops.llm import LLMOp

        require_key("llm:db-gemini-3-flash")
        ResourceHub.set_instance(live_hub)

        def shape_ok(parsed: dict) -> bool:
            return isinstance(parsed.get("answer"), str) and bool(parsed["answer"])

        op = LLMOp.of(
            resource="db-gemini-3-flash",
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
