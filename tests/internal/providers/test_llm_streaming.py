"""Tests for LLMOp streaming mode — async generator via _stream_core."""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon

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
        from operonx.providers.ops import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        op = LLMOp(name="test", resource="gpt-4o", stream=True)
        op._ensure_initialized()
        assert inspect.isasyncgenfunction(op.core)

    def test_non_stream_core_is_not_generator(self, hub):
        """When stream=False, self.core is NOT a generator."""
        from operonx.providers.ops import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        op = LLMOp(name="test", resource="gpt-4o", stream=False)
        op._ensure_initialized()
        assert not inspect.isasyncgenfunction(op.core)


# =============================================================================
# Test 2: _stream_core yields per-token dicts
# =============================================================================


class TestStreamCoreYields:
    @pytest.mark.asyncio
    async def test_yields_per_token(self, hub):
        """_stream_core yields one dict per content chunk + a final metadata dict."""
        from operonx.providers.ops import LLMOp

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
        op._llms = [mock_llm]
        op._initialized = True

        results = []
        async for item in op._stream_core(messages=[{"role": "user", "content": "hi"}]):
            results.append(item)

        # 2 token yields + 1 final metadata yield
        assert len(results) == 3

        # Token yields
        assert results[0] == {"content": "Hello", "role": "assistant", "final": False}
        assert results[1] == {"content": " world", "role": "assistant", "final": False}

        # Final yield has complete metadata
        final = results[2]
        assert final["content"] == "Hello world"
        assert final["model_used"] == "gpt-4o"
        assert final["finish_reason"] == "stop"
        assert final["usage"]["prompt_tokens"] == 5

    @pytest.mark.asyncio
    async def test_the_last_frame_repeats_the_whole_content(self, hub):
        """F8 — joining every frame's ``content`` double-counts the answer.

        The last frame carries the accumulated text, not a tail, and it
        arrives through the same channel as the deltas. ``final`` is the
        only thing that separates them; before it, consumers had to notice
        that ``finish_reason`` happened to be set.
        """
        from operonx.providers.ops import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        op = LLMOp(name="test", resource="gpt-4o", stream=True)
        op._llms = [
            make_mock_llm(
                [
                    make_chunk(content="Hel"),
                    make_chunk(content="lo"),
                    make_chunk(finish_reason="stop"),
                ]
            )
        ]
        op._initialized = True

        results = [r async for r in op._stream_core(messages=[{"role": "user", "content": "hi"}])]

        naive = "".join(r["content"] for r in results)
        assert naive == "HelloHello", "the duplication is real, not hypothetical"

        deltas = "".join(r["content"] for r in results if not r["final"])
        assert deltas == "Hello"
        assert next(r for r in results if r["final"])["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_empty_stream_yields_final_only(self, hub):
        """Stream with no content chunks yields only the final metadata."""
        from operonx.providers.ops import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        chunks = [
            make_chunk(finish_reason="stop"),
        ]

        op = LLMOp(name="test", resource="gpt-4o", stream=True)
        mock_llm = make_mock_llm(chunks)
        op._llms = [mock_llm]
        op._initialized = True

        results = []
        async for item in op._stream_core(messages=[{"role": "user", "content": "hi"}]):
            results.append(item)

        assert len(results) == 1
        assert results[0]["content"] == ""
        assert results[0]["finish_reason"] == "stop"


# =============================================================================
# Test 3: Streaming LLMOp in a graph via engine.start()
# =============================================================================


class TestStreamingLLMInGraph:
    @pytest.mark.asyncio
    async def test_streaming_llm_yields_tokens_via_engine(self, hub):
        """LLMOp(stream=True) in a graph delivers frames via engine.start()."""
        from operonx.providers.ops import LLMOp

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
        llm._llms = [mock_llm]
        llm._initialized = True

        engine = Operon(g)
        handle = engine.start(inputs={"messages": [{"role": "user", "content": "hi"}]})
        frames = []
        async for op, ctx, data in handle:
            frames.append((op, ctx, data))

        # Filter frames from the llm op
        llm_frames = [(op, ctx, data) for op, ctx, data in frames if op == "llm"]

        # 3 yields from generator (2 tokens + 1 final metadata)
        assert len(llm_frames) == 3

        # First two are token deltas
        assert llm_frames[0][2]["content"] == "Hello"
        assert llm_frames[1][2]["content"] == " world"

    @pytest.mark.asyncio
    async def test_streaming_llm_run_returns_accumulated(self, hub):
        """LLMOp(stream=True) via engine.run() returns accumulated result.

        engine.run() delegates to engine.start().collect() internally —
        all yielded frames are merged into a single output dict.
        """
        from operonx.providers.ops import LLMOp

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
        llm._llms = [mock_llm]
        llm._initialized = True

        engine = Operon(g)
        result = await engine.run(inputs={"messages": [{"role": "user", "content": "hi"}]})

        # run() collects all yields — last yield (final metadata) is the output
        # The streaming op yields multiple times; final result contains accumulated content
        assert "content" in result


# =============================================================================
# Test 4: Streaming with thinking content
# =============================================================================


class TestStreamingWithThinking:
    @pytest.mark.asyncio
    async def test_thinking_content_accumulated(self, hub):
        """Reasoning content is accumulated in the final yield."""
        from operonx.providers.ops import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        chunks = [
            make_chunk(reasoning_content="Let me think..."),
            make_chunk(content="The answer is 42"),
            make_chunk(finish_reason="stop"),
        ]

        op = LLMOp(name="test", resource="gpt-4o", stream=True)
        mock_llm = make_mock_llm(chunks)
        op._llms = [mock_llm]
        op._initialized = True

        results = []
        async for item in op._stream_core(messages=[{"role": "user", "content": "think"}]):
            results.append(item)

        # 1 content token + 1 final
        assert len(results) == 2
        assert results[0]["content"] == "The answer is 42"

        final = results[1]
        assert final["extras"]["thinking_content"] == "Let me think..."
        assert final["content"] == "The answer is 42"


# =============================================================================
# Test 5: Streaming fallback
# =============================================================================


class TestStreamingFallback:
    @pytest.mark.asyncio
    async def test_fallback_on_primary_failure(self, hub):
        """When primary LLM fails during streaming, fallback LLM is used."""
        from operonx.providers.ops import LLMOp

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
        llm_op._llms = [failing_llm]
        llm_op.fallback = ["fallback-model"]
        llm_op._fallback_llms = [fallback_llm]
        llm_op._initialized = True

        results = []
        async for item in llm_op._stream_core(messages=[{"role": "user", "content": "hi"}]):
            results.append(item)

        # Should get fallback response
        assert len(results) == 2  # 1 token + 1 final
        assert results[0]["content"] == "Fallback response"
        assert results[1]["model_used"] == "fallback-model"
