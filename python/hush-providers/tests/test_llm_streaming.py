"""Tests for LLMOp streaming mode — async generator via _stream_core."""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from hush.core import END, PARENT, START, GraphOp, Hush

# =============================================================================
# Mock Helpers
# =============================================================================


def make_chunk(content=None, finish_reason=None, usage=None, reasoning_content=None):
    """Create a mock ChatCompletionChunk."""
    delta = SimpleNamespace(
        content=content,
        tool_calls=None,
        reasoning_content=reasoning_content,
    )
    # Add refusal attr
    delta.refusal = None

    choice = SimpleNamespace(
        delta=delta,
        finish_reason=finish_reason,
    )
    has_choice = content or finish_reason or reasoning_content
    chunk = SimpleNamespace(
        choices=[choice] if has_choice else [],
        usage=SimpleNamespace(model_dump=lambda: usage) if usage else None,
    )
    return chunk


def make_mock_llm(chunks):
    """Create a mock LLM that yields given chunks from stream()."""
    mock_llm = MagicMock()
    mock_llm.config = SimpleNamespace(
        cost_per_input_token=None,
        cost_per_output_token=None,
    )

    async def mock_stream(**kwargs):
        for chunk in chunks:
            yield chunk

    mock_llm.stream = mock_stream
    return mock_llm


# =============================================================================
# Test 1: _stream_core is an async generator
# =============================================================================


class TestStreamCoreIsGenerator:
    def test_stream_core_is_async_gen(self, hub):
        """When stream=True, self.core is an async generator function."""
        from hush.providers.ops import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        op = LLMOp(name="test", resource="gpt-4o", stream=True)
        assert inspect.isasyncgenfunction(op.core)

    def test_non_stream_core_is_not_generator(self, hub):
        """When stream=False, self.core is NOT a generator."""
        from hush.providers.ops import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        op = LLMOp(name="test", resource="gpt-4o", stream=False)
        assert not inspect.isasyncgenfunction(op.core)


# =============================================================================
# Test 2: _stream_core yields per-token dicts
# =============================================================================


class TestStreamCoreYields:
    @pytest.mark.asyncio
    async def test_yields_per_token(self, hub):
        """_stream_core yields one dict per content chunk + a final metadata dict."""
        from hush.providers.ops import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        chunks = [
            make_chunk(content="Hello"),
            make_chunk(content=" world"),
            make_chunk(
                finish_reason="stop",
                usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            ),
        ]

        op = LLMOp(name="test", resource="gpt-4o", stream=True)
        mock_llm = make_mock_llm(chunks)
        op._llm = mock_llm
        op._llms = [mock_llm]

        results = []
        async for item in op._stream_core(messages=[{"role": "user", "content": "hi"}]):
            results.append(item)

        # 2 token yields + 1 final metadata yield
        assert len(results) == 3

        # Token yields
        assert results[0] == {"content": "Hello", "role": "assistant"}
        assert results[1] == {"content": " world", "role": "assistant"}

        # Final yield has complete metadata
        final = results[2]
        assert final["content"] == "Hello world"
        assert final["model_used"] == "gpt-4o"
        assert final["finish_reason"] == "stop"
        assert final["tokens_used"]["prompt_tokens"] == 5

    @pytest.mark.asyncio
    async def test_empty_stream_yields_final_only(self, hub):
        """Stream with no content chunks yields only the final metadata."""
        from hush.providers.ops import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        chunks = [
            make_chunk(finish_reason="stop"),
        ]

        op = LLMOp(name="test", resource="gpt-4o", stream=True)
        mock_llm = make_mock_llm(chunks)
        op._llm = mock_llm
        op._llms = [mock_llm]

        results = []
        async for item in op._stream_core(messages=[{"role": "user", "content": "hi"}]):
            results.append(item)

        assert len(results) == 1
        assert results[0]["content"] == ""
        assert results[0]["finish_reason"] == "stop"


# =============================================================================
# Test 3: Streaming LLMOp in a graph via engine.stream()
# =============================================================================


class TestStreamingLLMInGraph:
    @pytest.mark.asyncio
    async def test_streaming_llm_yields_tokens_via_engine(self, hub):
        """LLMOp(stream=True) in a graph delivers token events via engine.stream()."""
        from hush.providers.ops import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        chunks = [
            make_chunk(content="Hello"),
            make_chunk(content=" world"),
            make_chunk(
                finish_reason="stop",
                usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            ),
        ]

        with GraphOp(name="llm_stream") as g:
            llm = LLMOp(
                name="llm",
                resource="gpt-4o",
                stream=True,
                inputs={"messages": PARENT["messages"]},
            )
            START >> llm >> END

        # Patch the LLM backend before build
        mock_llm = make_mock_llm(chunks)
        llm._llm = mock_llm
        llm._llms = [mock_llm]

        engine = Hush(g)
        events = []
        async for event in engine.stream(inputs={"messages": [{"role": "user", "content": "hi"}]}):
            events.append(event)

        token_events = [e for e in events if e["type"] == "token"]
        done_events = [e for e in events if e["type"] == "done"]

        # 2 content tokens + 1 final metadata = 3 yields from generator
        assert len(token_events) == 3
        assert len(done_events) == 1

        # First two tokens are deltas
        assert token_events[0]["data"]["content"] == "Hello"
        assert token_events[1]["data"]["content"] == " world"

        # op name is present
        assert token_events[0]["op"] == "llm"

    @pytest.mark.asyncio
    async def test_streaming_llm_run_returns_accumulated(self, hub):
        """LLMOp(stream=True) via engine.run() returns accumulated result."""
        from hush.providers.ops import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        chunks = [
            make_chunk(content="Hello"),
            make_chunk(content=" world"),
            make_chunk(
                finish_reason="stop",
                usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            ),
        ]

        with GraphOp(name="llm_run") as g:
            llm = LLMOp(
                name="llm",
                resource="gpt-4o",
                stream=True,
                inputs={"messages": PARENT["messages"]},
            )
            START >> llm >> END

        mock_llm = make_mock_llm(chunks)
        llm._llm = mock_llm
        llm._llms = [mock_llm]

        engine = Hush(g)
        result = await engine.run(inputs={"messages": [{"role": "user", "content": "hi"}]})

        # run() collects all yields — last yield (final metadata) is the leaf context output
        # The graph has streaming ops, so outputs are lists
        assert isinstance(result["content"], list)
        assert len(result["content"]) == 3  # 3 yields from generator


# =============================================================================
# Test 4: Streaming with thinking content
# =============================================================================


class TestStreamingWithThinking:
    @pytest.mark.asyncio
    async def test_thinking_content_accumulated(self, hub):
        """Reasoning content is accumulated in the final yield."""
        from hush.providers.ops import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        chunks = [
            make_chunk(reasoning_content="Let me think..."),
            make_chunk(content="The answer is 42"),
            make_chunk(finish_reason="stop"),
        ]

        op = LLMOp(name="test", resource="gpt-4o", stream=True)
        mock_llm = make_mock_llm(chunks)
        op._llm = mock_llm
        op._llms = [mock_llm]

        results = []
        async for item in op._stream_core(messages=[{"role": "user", "content": "think"}]):
            results.append(item)

        # 1 content token + 1 final
        assert len(results) == 2
        assert results[0]["content"] == "The answer is 42"

        final = results[1]
        assert final["thinking_content"] == "Let me think..."
        assert final["content"] == "The answer is 42"


# =============================================================================
# Test 5: Streaming fallback
# =============================================================================


class TestStreamingFallback:
    @pytest.mark.asyncio
    async def test_fallback_on_primary_failure(self, hub):
        """When primary LLM fails during streaming, fallback LLM is used."""
        from hush.providers.ops import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        # Primary LLM that fails
        failing_llm = MagicMock()

        async def failing_stream(**kwargs):
            raise ConnectionError("primary down")
            yield  # noqa: F541 — unreachable yield makes this an async gen

        failing_llm.stream = failing_stream

        # Fallback LLM that works
        fallback_chunks = [
            make_chunk(content="Fallback response"),
            make_chunk(finish_reason="stop"),
        ]
        fallback_llm = make_mock_llm(fallback_chunks)

        # Create without fallback= to avoid hub lookup, then set manually
        llm_op = LLMOp(name="test", resource="gpt-4o", stream=True)
        llm_op._llm = failing_llm
        llm_op._llms = [failing_llm]
        llm_op.fallback = ["fallback-model"]
        llm_op._fallback_llms = [fallback_llm]

        results = []
        async for item in llm_op._stream_core(messages=[{"role": "user", "content": "hi"}]):
            results.append(item)

        # Should get fallback response
        assert len(results) == 2  # 1 token + 1 final
        assert results[0]["content"] == "Fallback response"
        assert results[1]["model_used"] == "fallback-model"
