"""A streaming fallback never contradicts deltas the consumer already has.

The deltas of a stream (``final=False``) and its last frame (``final=True``)
are two ways to read one answer, and the docs promise they agree. A
primary that failed *after* emitting deltas broke that: its frames had
already reached the consumer, then the fallback replayed its whole answer
from the start —

    joined deltas: "Sure, your appointment isSure, your appointment is Monday."
    final frame:   "Sure, your appointment is Monday."

— and a voice app, which speaks deltas as they arrive, said the opening
twice. So a fallback is taken only while nothing has been emitted; after
that the error propagates.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from operonx.providers.ops import LLMOp

pytestmark = pytest.mark.unit

MESSAGES = [{"role": "user", "content": "hi"}]


def chunk(content=None, finish_reason=None, reasoning_content=None):
    delta = SimpleNamespace(
        content=content, tool_calls=None, reasoning_content=reasoning_content, refusal=None
    )
    has_choice = content or finish_reason or reasoning_content
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)] if has_choice else [],
        usage=None,
    )


class _LLM:
    """Streams scripted chunks, then optionally raises."""

    def __init__(self, *chunks, fail: Exception | None = None):
        self.chunks, self.fail, self.calls = chunks, fail, 0
        self.config = SimpleNamespace(cost_per_input_token=None, cost_per_output_token=None)

    async def stream(self, **kwargs):
        self.calls += 1
        for c in self.chunks:
            yield c
        if self.fail is not None:
            raise self.fail


def _op(primary: _LLM, *fallbacks: _LLM) -> LLMOp:
    op = LLMOp(name="s", resource="primary", stream=True)
    op._llms = [primary]
    op.fallback = [f"fb{i}" for i in range(len(fallbacks))]
    op._fallback_llms = list(fallbacks)
    op._initialized = True
    return op


async def _collect(op: LLMOp):
    frames = []
    try:
        async for frame in op._stream_core(messages=MESSAGES):
            frames.append(frame)
    except Exception as e:  # the frames that got out before it matter too
        return frames, e
    return frames, None


def _joined(frames):
    return "".join(f["content"] for f in frames if not f["final"])


class TestFailureAfterTheFirstDelta:
    @pytest.mark.asyncio
    async def test_the_error_propagates_instead_of_a_replay(self):
        primary = _LLM(
            chunk("Sure, your "), chunk("appointment is"), fail=ConnectionError("dropped")
        )
        fallback = _LLM(chunk("Sure, your appointment is Monday."), chunk(finish_reason="stop"))
        frames, error = await _collect(_op(primary, fallback))

        assert isinstance(error, ConnectionError)
        assert fallback.calls == 0, "a replay would repeat what the consumer already has"
        assert _joined(frames) == "Sure, your appointment is"
        assert not any(f["final"] for f in frames)

    @pytest.mark.asyncio
    async def test_a_fallback_that_fails_mid_stream_is_not_followed_by_the_next(self):
        primary = _LLM(fail=ConnectionError("down"))
        first = _LLM(chunk("Hel"), fail=TimeoutError("cut"))
        second = _LLM(chunk("Hello"), chunk(finish_reason="stop"))
        frames, error = await _collect(_op(primary, first, second))

        assert isinstance(error, TimeoutError)
        assert second.calls == 0
        assert _joined(frames) == "Hel"


class TestFailureBeforeAnyDelta:
    @pytest.mark.asyncio
    async def test_falls_back_and_deltas_agree_with_the_final_frame(self):
        primary = _LLM(fail=ConnectionError("refused"))
        fallback = _LLM(chunk("Mon"), chunk("day."), chunk(finish_reason="stop"))
        frames, error = await _collect(_op(primary, fallback))

        assert error is None
        final = [f for f in frames if f["final"]]
        assert len(final) == 1
        assert _joined(frames) == final[0]["content"] == "Monday."
        assert final[0]["model_used"] == "fb0"

    @pytest.mark.asyncio
    async def test_reasoning_alone_does_not_count_as_emitted(self):
        """Reasoning deltas are accumulated, not yielded — the consumer has
        seen nothing, so falling back contradicts nothing."""
        primary = _LLM(chunk(reasoning_content="thinking..."), fail=ConnectionError("dropped"))
        fallback = _LLM(chunk("Hi."), chunk(finish_reason="stop"))
        frames, error = await _collect(_op(primary, fallback))

        assert error is None
        assert _joined(frames) == next(f for f in frames if f["final"])["content"] == "Hi."

    @pytest.mark.asyncio
    async def test_a_fallback_failing_before_any_delta_moves_to_the_next(self):
        primary = _LLM(fail=ConnectionError("down"))
        first = _LLM(fail=TimeoutError("down too"))
        second = _LLM(chunk("Hello"), chunk(finish_reason="stop"))
        frames, error = await _collect(_op(primary, first, second))

        assert error is None
        assert _joined(frames) == next(f for f in frames if f["final"])["content"] == "Hello"
        assert next(f for f in frames if f["final"])["model_used"] == "fb1"


class TestNoFallbackConfigured:
    @pytest.mark.asyncio
    async def test_the_error_propagates_unchanged(self):
        primary = _LLM(chunk("Hel"), fail=ConnectionError("dropped"))
        frames, error = await _collect(_op(primary))
        assert isinstance(error, ConnectionError)
        assert _joined(frames) == "Hel"
