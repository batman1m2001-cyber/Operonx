"""Tests for Anthropic LLM provider — real API calls + unit tests."""

import os
import pytest

from hush.providers.llms.anthropic import AnthropicModel
from hush.providers.llms.config import AnthropicConfig, LLMConfig, LLMType
from hush.providers.llms.factory import create_llm


# ── Load API key ────────────────────────────────────────────────────────

# Try loading from .env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))
except ImportError:
    pass

API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SKIP_REASON = "ANTHROPIC_API_KEY not set"


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def config():
    return AnthropicConfig(
        api_key=API_KEY,
        base_url="https://api.anthropic.com",
        model="claude-3-haiku-20240307",
    )


@pytest.fixture
def model(config):
    return AnthropicModel(config)


# ── Config tests ────────────────────────────────────────────────────────


def test_config_defaults():
    config = AnthropicConfig(api_key="sk-test", model="claude-3-haiku-20240307")
    assert config.api_type == LLMType.ANTHROPIC
    assert config.base_url == "https://api.anthropic.com"
    assert config.anthropic_version == "2023-06-01"


def test_config_from_dict():
    config = LLMConfig.create_config({
        "api_type": "anthropic",
        "api_key": "sk-test",
        "model": "claude-3-haiku-20240307",
    })
    assert isinstance(config, AnthropicConfig)


def test_factory_creates_anthropic():
    config = AnthropicConfig(api_key="test", model="claude-3-haiku-20240307")
    m = create_llm(config)
    assert isinstance(m, AnthropicModel)


# ── Message conversion ─────────────────────────────────────────────────


def test_convert_messages_extracts_system():
    system, msgs = AnthropicModel._convert_messages([
        {"role": "system", "content": "Be helpful."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "Bye"},
    ])
    assert system == "Be helpful."
    assert len(msgs) == 3
    assert msgs[0]["role"] == "user"


def test_convert_messages_no_system():
    system, msgs = AnthropicModel._convert_messages([
        {"role": "user", "content": "Hi"},
    ])
    assert system is None
    assert len(msgs) == 1


# ── Stop reason mapping ────────────────────────────────────────────────


def test_stop_reason_mapping():
    m = AnthropicModel._map_stop_reason
    assert m("end_turn") == "stop"
    assert m("max_tokens") == "length"
    assert m("stop_sequence") == "stop"
    assert m("tool_use") == "tool_calls"
    assert m(None) == "stop"


# ── Request building ───────────────────────────────────────────────────


def test_build_request(model):
    body = model._build_request(
        [{"role": "system", "content": "X"}, {"role": "user", "content": "Y"}],
        temperature=0.5, max_tokens=100, stop=["END"],
    )
    assert body["system"] == "X"
    assert body["messages"] == [{"role": "user", "content": "Y"}]
    assert body["temperature"] == 0.5
    assert body["max_tokens"] == 100
    assert body["stop_sequences"] == ["END"]


# ── Response conversion ────────────────────────────────────────────────


def test_to_chat_completion(model):
    result = model._to_chat_completion({
        "id": "msg_123",
        "model": "claude-3-haiku-20240307",
        "content": [{"type": "text", "text": "Hello!"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })
    assert result.choices[0].message.content == "Hello!"
    assert result.choices[0].finish_reason == "stop"
    assert result.usage.total_tokens == 15


# ── Chunk conversion ───────────────────────────────────────────────────


def test_to_chunk_text_delta(model):
    chunk = model._to_chunk(
        "content_block_delta",
        {"delta": {"type": "text_delta", "text": "Hi"}},
        "claude-3-haiku", "id_1",
    )
    assert chunk.choices[0].delta.content == "Hi"


def test_to_chunk_message_delta(model):
    chunk = model._to_chunk(
        "message_delta",
        {"delta": {"stop_reason": "end_turn"}, "usage": {"input_tokens": 10, "output_tokens": 5}},
        "claude-3-haiku", "id_1",
    )
    assert chunk.choices[0].finish_reason == "stop"


def test_to_chunk_irrelevant_events(model):
    assert model._to_chunk("ping", {}, "m", "id") is None
    assert model._to_chunk("content_block_start", {}, "m", "id") is None
    assert model._to_chunk("message_stop", {}, "m", "id") is None


# ═══════════════════════════════════════════════════════════════════════
#                    REAL API TESTS
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not API_KEY, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_generate_real(model):
    """Call real Anthropic API — non-streaming."""
    result = await model.generate(
        messages=[{"role": "user", "content": "Say 'hello' and nothing else."}],
        max_tokens=20,
        temperature=0.0,
    )
    assert result.choices[0].message.content
    assert "hello" in result.choices[0].message.content.lower()
    assert result.choices[0].finish_reason == "stop"
    assert result.usage.prompt_tokens > 0
    assert result.usage.completion_tokens > 0
    print(f"\n  generate: {result.choices[0].message.content}")
    print(f"  tokens: {result.usage.prompt_tokens}→{result.usage.completion_tokens}")


@pytest.mark.skipif(not API_KEY, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_stream_real(model):
    """Call real Anthropic API — streaming."""
    chunks = []
    async for chunk in model.stream(
        messages=[{"role": "user", "content": "Count from 1 to 5."}],
        max_tokens=50,
        temperature=0.0,
    ):
        chunks.append(chunk)

    assert len(chunks) > 0

    # Collect streamed text
    text = "".join(
        c.choices[0].delta.content
        for c in chunks
        if c.choices[0].delta.content
    )
    assert text  # Should have some text
    print(f"\n  stream: {text}")
    print(f"  chunks: {len(chunks)}")

    # Last chunk should have finish_reason
    assert chunks[-1].choices[0].finish_reason == "stop"


@pytest.mark.skipif(not API_KEY, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_generate_with_system_prompt(model):
    """Test system prompt is handled correctly."""
    result = await model.generate(
        messages=[
            {"role": "system", "content": "You must respond with exactly one word."},
            {"role": "user", "content": "What color is the sky?"},
        ],
        max_tokens=10,
        temperature=0.0,
    )
    content = result.choices[0].message.content.strip()
    assert len(content.split()) <= 3  # Should be very short
    print(f"\n  system prompt test: {content}")


@pytest.mark.skipif(not API_KEY, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_generate_max_tokens(model):
    """Test max_tokens truncation."""
    result = await model.generate(
        messages=[{"role": "user", "content": "Write a long essay about AI."}],
        max_tokens=5,
        temperature=0.0,
    )
    assert result.choices[0].finish_reason == "length"
    assert result.usage.completion_tokens <= 10  # roughly 5 tokens
    print(f"\n  max_tokens test: '{result.choices[0].message.content}'")


if __name__ == "__main__":
    import asyncio

    async def main():
        if not API_KEY:
            print(f"Set ANTHROPIC_API_KEY to run. {SKIP_REASON}")
            return

        config = AnthropicConfig(
            api_key=API_KEY,
            model="claude-3-haiku-20240307",
        )
        model = AnthropicModel(config)

        print("=== Generate ===")
        result = await model.generate(
            messages=[{"role": "user", "content": "Say hello in Vietnamese."}],
            max_tokens=30,
        )
        print(f"  {result.choices[0].message.content}")
        print(f"  tokens: {result.usage}")

        print("\n=== Stream ===")
        async for chunk in model.stream(
            messages=[{"role": "user", "content": "Count 1 to 3 in Vietnamese."}],
            max_tokens=50,
        ):
            if chunk.choices[0].delta.content:
                print(chunk.choices[0].delta.content, end="", flush=True)
        print()

    asyncio.run(main())
