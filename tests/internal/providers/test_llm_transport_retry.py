"""Transport retry + per-resource generation_extras on LLMOp.

This is the retry that answers a *gateway* — 429, 5xx, a dropped
connection, or an HTTP 200 carrying empty content. It is distinct from
``LLMOp.max_retries``, which re-asks the model after a parser or
validator rejects a well-formed answer; a rate-limited endpoint needs
backoff, not a re-prompt.
"""

import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest

from operonx.providers.llms.config import LLMType, OpenAIConfig
from operonx.providers.ops.llm import LLMOp, _is_empty_completion


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def _completion(content):
    """A ChatCompletion-shaped object, minus the SDK."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _config(**overrides):
    base = dict(
        api_type=LLMType.OPENAI,
        api_key="k",
        base_url="http://localhost",
        model="m",
        max_retries=3,
        retry_base_delay=0.001,
        retry_min_delay=0.0,
        retry_max_delay=0.002,
    )
    base.update(overrides)
    return OpenAIConfig(**base)


def _llm(config):
    return SimpleNamespace(config=config)


def _rate_limit_error(retry_after=None):
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(
        429, headers=headers, request=httpx.Request("POST", "http://localhost")
    )
    return openai.RateLimitError("slow down", response=response, body=None)


def _status_error(code):
    response = httpx.Response(
        code, request=httpx.Request("POST", "http://localhost")
    )
    return openai.APIStatusError("boom", response=response, body=None)


class _Op(LLMOp):
    """LLMOp with construction skipped — only the retry layer is exercised."""

    def __init__(self):  # noqa: D107 - deliberately not calling super()
        self.name = "test_op"


# ---------------------------------------------------------------------------
# _is_empty_completion
# ---------------------------------------------------------------------------


class TestIsEmptyCompletion:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (_completion(None), True),
            (_completion(""), True),
            (_completion("   "), True),
            (_completion("hello"), False),
            ({"content": None}, True),
            ({"content": ""}, True),
            ({"content": "hi"}, False),
        ],
    )
    def test_content_slot(self, value, expected):
        assert _is_empty_completion(value) is expected

    def test_unknown_shape_is_not_empty(self):
        """An unfamiliar-but-valid response must not be retried into a limit."""
        assert _is_empty_completion(object()) is False
        assert _is_empty_completion(SimpleNamespace(choices=[])) is False


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


class TestCallWithRetry:
    @pytest.mark.asyncio
    async def test_succeeds_without_retry(self):
        calls = []

        async def fn(**kw):
            calls.append(kw)
            return _completion("ok")

        out = await _Op()._call_with_retry(fn, llm=_llm(_config()))
        assert out.choices[0].message.content == "ok"
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_retries_rate_limit_then_succeeds(self):
        attempts = {"n": 0}

        async def fn(**kw):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise _rate_limit_error()
            return _completion("ok")

        out = await _Op()._call_with_retry(fn, llm=_llm(_config()))
        assert out.choices[0].message.content == "ok"
        assert attempts["n"] == 3

    @pytest.mark.asyncio
    async def test_raises_after_budget_exhausted(self):
        async def fn(**kw):
            raise _rate_limit_error()

        with pytest.raises(openai.RateLimitError):
            await _Op()._call_with_retry(fn, llm=_llm(_config(max_retries=2)))

    @pytest.mark.asyncio
    async def test_disabled_by_default(self):
        """max_retries=0 means one attempt — the operonx default."""
        attempts = {"n": 0}

        async def fn(**kw):
            attempts["n"] += 1
            raise _rate_limit_error()

        with pytest.raises(openai.RateLimitError):
            await _Op()._call_with_retry(fn, llm=_llm(_config(max_retries=0)))
        assert attempts["n"] == 1

    @pytest.mark.asyncio
    async def test_retries_5xx_but_not_4xx(self):
        """A 4xx is the caller's fault — retrying only burns the quota."""
        server = {"n": 0}

        async def flaky(**kw):
            server["n"] += 1
            if server["n"] == 1:
                raise _status_error(503)
            return _completion("ok")

        out = await _Op()._call_with_retry(flaky, llm=_llm(_config()))
        assert out.choices[0].message.content == "ok"
        assert server["n"] == 2

        client = {"n": 0}

        async def bad_request(**kw):
            client["n"] += 1
            raise _status_error(400)

        with pytest.raises(openai.APIStatusError):
            await _Op()._call_with_retry(bad_request, llm=_llm(_config()))
        assert client["n"] == 1

    @pytest.mark.asyncio
    async def test_retries_transport_errors(self):
        attempts = {"n": 0}

        async def fn(**kw):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise openai.APITimeoutError(
                    request=httpx.Request("POST", "http://localhost")
                )
            return _completion("ok")

        await _Op()._call_with_retry(fn, llm=_llm(_config()))
        assert attempts["n"] == 2

    @pytest.mark.asyncio
    async def test_retries_empty_200(self):
        """Anthropic answers 200 with null content under overload."""
        attempts = {"n": 0}

        async def fn(**kw):
            attempts["n"] += 1
            return _completion(None if attempts["n"] < 3 else "finally")

        out = await _Op()._call_with_retry(fn, llm=_llm(_config()))
        assert out.choices[0].message.content == "finally"
        assert attempts["n"] == 3

    @pytest.mark.asyncio
    async def test_empty_200_opt_out(self):
        attempts = {"n": 0}

        async def fn(**kw):
            attempts["n"] += 1
            return _completion(None)

        out = await _Op()._call_with_retry(
            fn, llm=_llm(_config(retry_on_empty=False))
        )
        assert out.choices[0].message.content is None
        assert attempts["n"] == 1

    @pytest.mark.asyncio
    async def test_exhausted_empty_returns_rather_than_raises(self):
        """The caller — a fallback chain or a validator — decides what an
        empty answer means, so this path returns instead of raising."""
        attempts = {"n": 0}

        async def fn(**kw):
            attempts["n"] += 1
            return _completion(None)

        out = await _Op()._call_with_retry(fn, llm=_llm(_config(max_retries=2)))
        assert out.choices[0].message.content is None
        assert attempts["n"] == 3

    @pytest.mark.asyncio
    async def test_kwargs_forwarded(self):
        seen = {}

        async def fn(**kw):
            seen.update(kw)
            return _completion("ok")

        await _Op()._call_with_retry(
            fn, llm=_llm(_config()), messages=[{"role": "user", "content": "x"}]
        )
        assert seen["messages"] == [{"role": "user", "content": "x"}]


class TestRetryDelay:
    def test_honours_retry_after_header(self):
        delay = _Op()._retry_delay(_rate_limit_error("7"), 1.0, 0.0, 60.0, 0)
        assert delay == 7.0

    def test_falls_back_to_jitter_without_header(self):
        delay = _Op()._retry_delay(_rate_limit_error(), 4.0, 1.0, 10.0, 0)
        assert 1.0 <= delay <= 10.0

    def test_jitter_respects_floor_and_cap(self):
        for attempt in range(6):
            d = LLMOp._jitter(base_delay=5.0, min_delay=2.0, max_delay=9.0, attempt=attempt)
            assert 2.0 <= d <= 9.0


# ---------------------------------------------------------------------------
# generation_extras
# ---------------------------------------------------------------------------


class TestGenerationExtras:
    def test_absent_returns_params_unchanged(self):
        params = {"messages": []}
        out = LLMOp._merge_generation_extras(_llm(_config()), params)
        assert out is params

    def test_merged_into_call(self):
        llm = _llm(_config(generation_extras={"reasoning_effort": "high"}))
        out = LLMOp._merge_generation_extras(llm, {"messages": []})
        assert out["reasoning_effort"] == "high"

    def test_call_site_wins_over_resource_default(self):
        llm = _llm(_config(generation_extras={"max_tokens": 100}))
        out = LLMOp._merge_generation_extras(llm, {"max_tokens": 4096})
        assert out["max_tokens"] == 4096

    def test_null_reaches_params_so_prepare_can_strip_it(self):
        """``top_p: null`` must arrive as None — `_prepare_params` drops it,
        which is how a resource removes a key the model refuses to accept
        alongside another (Claude 4.6: temperature + top_p together)."""
        llm = _llm(_config(generation_extras={"top_p": None}))
        out = LLMOp._merge_generation_extras(llm, {"messages": []})
        assert "top_p" in out and out["top_p"] is None

    def test_does_not_mutate_the_caller_dict(self):
        """Each fallback resource merges its own knobs off the same base."""
        base = {"messages": []}
        llm = _llm(_config(generation_extras={"reasoning_effort": "low"}))
        LLMOp._merge_generation_extras(llm, base)
        assert base == {"messages": []}


class TestPrepareParamsStripping:
    """The other half of the null-strips-key contract."""

    def _prep(self, **kw):
        from operonx.providers.llms.base import BaseLLM

        return BaseLLM._prepare_params(
            SimpleNamespace(resolve_image_paths=lambda m: m),
            model="m",
            messages=[{"role": "user", "content": "x"}],
            stream=False,
            **kw,
        )

    def test_none_top_p_is_omitted(self):
        params = self._prep(temperature=0.0, top_p=None)
        assert "top_p" not in params
        assert params["temperature"] == 0.0

    def test_none_temperature_is_omitted(self):
        params = self._prep(temperature=None, top_p=0.1)
        assert "temperature" not in params
        assert params["top_p"] == 0.1

    def test_values_pass_through(self):
        params = self._prep(temperature=0.7, top_p=0.9)
        assert params["temperature"] == 0.7
        assert params["top_p"] == 0.9

    def test_none_extras_are_omitted(self):
        params = self._prep(temperature=0.0, top_p=0.1, thinking=None, seed=42)
        assert "thinking" not in params
        assert params["seed"] == 42
