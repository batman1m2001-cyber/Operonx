"""``batch_mode=True`` keeps the structured-output layer.

The batch path used to return straight after the coordinator answered,
ahead of the ``fields=`` check, so ``fields``, ``parser``, ``validators``
and ``max_retries`` did nothing. The declared outputs then resolved to
their defaults — every field ``None``, ``error`` ``None`` — which reads as
a clean parse of an answer the parser never saw.

Parsing and validating are pure work on the completion, so they apply to
a batch result exactly as to a live one; a semantic retry is one more
batch submission. ``fallback=`` is refused instead: the coordinator is
bound to the primary resource, and falling back would mean a live call
at full price — a different cost class than the caller chose.
"""

from __future__ import annotations

import pytest
from openai.types.chat.chat_completion import ChatCompletion, Choice
from openai.types.chat.chat_completion_message import ChatCompletionMessage

from operonx.providers.ops import LLMOp

pytestmark = pytest.mark.unit


def _completion(text: str) -> ChatCompletion:
    return ChatCompletion(
        id="batch-1",
        created=1,
        model="m",
        object="chat.completion",
        choices=[
            Choice(
                index=0,
                message=ChatCompletionMessage(role="assistant", content=text),
                finish_reason="stop",
            )
        ],
    )


class _Coordinator:
    """Answers ``submit`` from a script and records what it was sent."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.sent: list = []

    async def submit(self, **params):
        self.sent.append(params)
        return _completion(self.answers[min(len(self.sent), len(self.answers)) - 1])


def _batch_op(coordinator: _Coordinator, **kwargs) -> LLMOp:
    op = LLMOp(name="batch", resource="r", batch_mode=True, **kwargs)
    # Skip the hub: the coordinator is what the batch path talks to.
    op._batch_coordinator = coordinator
    op._llms = [object()]
    op._initialized = True
    return op


class TestStructuredLayerAppliesToBatch:
    @pytest.mark.asyncio
    async def test_fields_are_parsed(self):
        op = _batch_op(_Coordinator("<intent>book</intent>"), fields=["intent: str"])
        out = await op._generate_core(prompt="Classify: hi")
        assert out["intent"] == "book"
        assert out["error"] is None
        assert out["content"] == "<intent>book</intent>"

    @pytest.mark.asyncio
    async def test_a_rejected_answer_reports_an_error(self):
        op = _batch_op(
            _Coordinator("<intent>cancel</intent>"),
            fields=["intent: str"],
            validators={"intent": ["book"]},
        )
        out = await op._generate_core(prompt="Classify: hi")
        assert out["intent"] is None
        assert out["error"] and "cancel" in out["error"]

    @pytest.mark.asyncio
    async def test_a_semantic_retry_is_another_batch_submission(self):
        coordinator = _Coordinator("<oops>", "<intent>book</intent>")
        op = _batch_op(coordinator, fields=["intent: str"], max_retries=1)
        out = await op._generate_core(prompt="Classify: hi")
        assert out["intent"] == "book" and out["error"] is None
        assert len(coordinator.sent) == 2
        # The retry carries the failed answer and the hint, as a live retry does.
        retry = coordinator.sent[1]["messages"]
        assert retry[1] == {"role": "assistant", "content": "<oops>"}
        assert "failed" in retry[2]["content"]

    @pytest.mark.asyncio
    async def test_raw_mode_is_unchanged(self):
        op = _batch_op(_Coordinator("hello"))
        out = await op._generate_core(prompt="hi")
        assert out["content"] == "hello"
        assert "error" not in out


class TestFallbackIsRefused:
    def test_batch_mode_with_fallback_raises_at_construction(self):
        with pytest.raises(ValueError, match="fallback"):
            LLMOp(name="b", resource="r", batch_mode=True, fallback=["other"])

    def test_the_shorthand_raises_too(self):
        with pytest.raises(ValueError, match="batch_mode"):
            LLMOp.of(resource="r", batch_mode=True, fallback=["other"], prompt="hi")
