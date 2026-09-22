"""Databricks-proxied LLM backends — message normalisation.

Both endpoints live on the same workspace host and speak OpenAI's wire
format, but they disagree about message shape. Getting it wrong on the
Gemini side returns ``401 - Credential was not sent or was of an
unsupported type``, which points at the token instead of the payload —
so these two classes exist to make the difference explicit, and these
tests pin it.

No network: the transformations are pure functions over the message list.
See ``test_live_resources.py`` for the calls that need a real endpoint.
"""

import pytest

from operonx.providers.llms.config import LLMConfig, LLMType, OpenAIConfig
from operonx.providers.llms.databricks import (
    ANTHROPIC_CACHE_CONTROL_LIMIT,
    DatabricksAnthropic,
    DatabricksGemini,
)
from operonx.providers.llms.factory import create_llm


def _cached(text, n=1):
    """A system message carrying *n* Anthropic cache breakpoints."""
    return {
        "role": "system",
        "content": [
            {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
            for _ in range(n)
        ],
    }


class TestConfigAndFactory:
    @pytest.mark.parametrize(
        "api_type,expected",
        [("db-anthropic", DatabricksAnthropic), ("db-gemini", DatabricksGemini)],
    )
    def test_factory_builds_the_right_backend(self, api_type, expected):
        config = LLMConfig.create_config(
            {
                "api_type": api_type,
                "api_key": "k",
                "base_url": "http://localhost/suffix",
                "model": "m",
            }
        )
        assert isinstance(config, OpenAIConfig)
        assert isinstance(create_llm(config), expected)

    def test_both_types_reachable_from_the_enum(self):
        assert LLMType("db-anthropic") is LLMType.DB_ANTHROPIC
        assert LLMType("db-gemini") is LLMType.DB_GEMINI

    def test_retry_and_extras_survive_config_creation(self):
        """These resources are the reason the knobs exist — a Databricks
        gateway rate-limits, and each model wants different vendor fields."""
        config = LLMConfig.create_config(
            {
                "api_type": "db-gemini",
                "api_key": "k",
                "base_url": "http://localhost/ai-gateway/mlflow/v1",
                "model": "system.ai.gemini-3-flash",
                "max_retries": 10,
                "retry_base_delay": 30.0,
                "retry_min_delay": 10.0,
                "retry_max_delay": 120.0,
                "generation_extras": {"reasoning_effort": "minimal"},
            }
        )
        assert config.max_retries == 10
        assert config.retry_base_delay == 30.0
        assert config.generation_extras == {"reasoning_effort": "minimal"}


class TestAnthropicCacheControl:
    def test_counts_breakpoints_across_messages(self):
        messages = [_cached("a", n=2), _cached("b", n=1), {"role": "user", "content": "x"}]
        assert DatabricksAnthropic._count_cache_breakpoints(messages) == 3

    def test_plain_string_content_counts_zero(self):
        messages = [{"role": "user", "content": "hello"}]
        assert DatabricksAnthropic._count_cache_breakpoints(messages) == 0

    def test_at_the_limit_is_allowed(self):
        llm = DatabricksAnthropic.__new__(DatabricksAnthropic)
        llm._validate_cache_control([_cached("a", n=ANTHROPIC_CACHE_CONTROL_LIMIT)])

    def test_over_the_limit_fails_locally(self):
        """Caught here so the message names the real problem — the API
        answers a 400 that does not."""
        llm = DatabricksAnthropic.__new__(DatabricksAnthropic)
        with pytest.raises(ValueError, match="cache_control limit exceeded"):
            llm._validate_cache_control([_cached("a", n=ANTHROPIC_CACHE_CONTROL_LIMIT + 1)])


class TestGeminiNormalisation:
    def test_flattens_multipart_text(self):
        out = DatabricksGemini._flatten_message(
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "first"},
                    {"type": "text", "text": "second"},
                ],
            }
        )
        assert out["content"] == "first\nsecond"
        assert out["role"] == "system"

    def test_drops_cache_control(self):
        """The field that makes this endpoint answer 401."""
        out = DatabricksGemini._flatten_message(_cached("prompt"))
        assert out["content"] == "prompt"
        assert "cache_control" not in str(out)

    def test_drops_non_text_parts(self):
        out = DatabricksGemini._flatten_message(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {"type": "image_url", "image_url": {"url": "http://x/y.png"}},
                ],
            }
        )
        assert out["content"] == "look"

    def test_plain_string_passes_through_unchanged(self):
        msg = {"role": "user", "content": "hello"}
        assert DatabricksGemini._flatten_message(msg) is msg

    def test_normalises_every_message(self):
        llm = DatabricksGemini.__new__(DatabricksGemini)
        out = llm._normalize_messages([_cached("sys"), {"role": "user", "content": "hi"}])
        assert [m["content"] for m in out] == ["sys", "hi"]

    def test_does_not_mutate_the_input(self):
        original = _cached("sys")
        llm = DatabricksGemini.__new__(DatabricksGemini)
        llm._normalize_messages([original])
        assert isinstance(original["content"], list)
