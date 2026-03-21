"""Tests for extract() retry mechanism.

Tests the actual retry loop behavior:
- Parser error → retry → success
- Validator reject → exhaust retries → default fallback
- No retry (retry=0) → single attempt
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from hush.core import Hush, GraphOp, START, END, PARENT
from hush.core.ops import op


# ── Helper: mock LLM that returns different content each call ────────


def make_mock_hub(responses):
    """Create a mock ResourceHub where LLM returns responses in order.

    Args:
        responses: list of content strings. Each call returns next one.
    """
    call_count = {"n": 0}

    async def mock_generate(messages, **kwargs):
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        content = responses[idx]

        # Return OpenAI-compatible ChatCompletion
        from openai.types.chat.chat_completion import ChatCompletion, Choice
        from openai.types.chat.chat_completion_message import ChatCompletionMessage
        from openai.types.completion_usage import CompletionUsage

        return ChatCompletion(
            id=f"mock-{idx}",
            created=1000000,
            model="mock-model",
            object="chat.completion",
            choices=[Choice(
                index=0,
                message=ChatCompletionMessage(role="assistant", content=content),
                finish_reason="stop",
            )],
            usage=CompletionUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    mock_llm = Mock()
    mock_llm.generate = mock_generate

    mock_hub = Mock()
    mock_hub.llm.return_value = mock_llm

    return mock_hub, call_count


# ── Test 1: Validator reject → exhaust retries → default fallback ────


@pytest.mark.asyncio
async def test_extract_validator_reject_uses_default():
    """LLM always returns valid XML but wrong value → validator rejects → default applied."""
    from hush.providers.ops.chain import extract

    # LLM always returns "UNKNOWN" — not in allowed list
    mock_hub, call_count = make_mock_hub([
        "<result>UNKNOWN</result>",
        "<result>UNKNOWN</result>",
        "<result>UNKNOWN</result>",
    ])

    with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub

        with GraphOp(name="test_workflow") as g:
            e = extract(
                resource="mock",
                template="Classify: {text}",
                fields=["result: str"],
                parser="xml",
                retry=2,
                validators={"result": ["CONFIRM", "DENY", "FALLBACK"]},
                default={"result": "FALLBACK"},
                text="some input",
            )
            START >> e >> END

        engine = Hush(g)
        result = await engine.run(inputs={})

    # Should have called LLM 3 times (1 + 2 retries)
    assert call_count["n"] == 3
    # Should fall back to default
    assert result["result"] == "FALLBACK"
    print(f"  validator reject test: result={result['result']}, calls={call_count['n']}")


# ── Test 2: Parse error first, success second → retry works ──────────


@pytest.mark.asyncio
async def test_extract_parse_fail_then_success():
    """First LLM call returns garbage → parse fail → retry → second call returns valid XML."""
    from hush.providers.ops.chain import extract

    mock_hub, call_count = make_mock_hub([
        "this is not xml at all!!!",       # attempt 1: parse fail
        "<result>CONFIRM</result>",         # attempt 2: parse OK + valid
    ])

    with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub

        with GraphOp(name="test_workflow") as g:
            e = extract(
                resource="mock",
                template="Classify: {text}",
                fields=["result: str"],
                parser="xml",
                retry=1,
                validators={"result": ["CONFIRM", "DENY"]},
                default={"result": "FALLBACK"},
                text="vâng đúng rồi",
            )
            START >> e >> END

        engine = Hush(g)
        result = await engine.run(inputs={})

    assert call_count["n"] == 2  # 2 attempts
    assert result["result"] == "CONFIRM"
    print(f"  retry success test: result={result['result']}, calls={call_count['n']}")


# ── Test 3: No retry (retry=0) → single attempt, fail → default ─────


@pytest.mark.asyncio
async def test_extract_no_retry_uses_default_on_fail():
    """retry=0 → only 1 attempt. Parse fail → default applied immediately."""
    from hush.providers.ops.chain import extract

    mock_hub, call_count = make_mock_hub([
        "garbage output",
    ])

    with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub

        with GraphOp(name="test_workflow") as g:
            e = extract(
                resource="mock",
                template="Classify: {text}",
                fields=["result: str"],
                parser="xml",
                retry=0,
                validators={"result": ["CONFIRM", "DENY", "FALLBACK"]},
                default={"result": "FALLBACK"},
                text="test",
            )
            START >> e >> END

        engine = Hush(g)
        result = await engine.run(inputs={})

    assert call_count["n"] == 1  # only 1 attempt
    assert result["result"] == "FALLBACK"
    print(f"  no retry test: result={result['result']}, calls={call_count['n']}")


# ── Test 4: First attempt succeeds → no retry needed ────────────────


@pytest.mark.asyncio
async def test_extract_success_no_retry_needed():
    """LLM returns valid output on first try → no retry triggered."""
    from hush.providers.ops.chain import extract

    mock_hub, call_count = make_mock_hub([
        "<intent>CONFIRM</intent>",
    ])

    with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub

        with GraphOp(name="test_workflow") as g:
            e = extract(
                resource="mock",
                template="Classify: {text}",
                fields=["intent: str"],
                parser="xml",
                retry=2,
                validators={"intent": ["CONFIRM", "DENY"]},
                text="vâng",
            )
            START >> e >> END

        engine = Hush(g)
        result = await engine.run(inputs={})

    assert call_count["n"] == 1  # only 1 call needed
    assert result["intent"] == "CONFIRM"
    print(f"  no retry needed test: intent={result['intent']}, calls={call_count['n']}")


# ── Test 5: Multiple fields extracted ────────────────────────────────


@pytest.mark.asyncio
async def test_extract_multiple_fields():
    """Extract multiple fields from XML."""
    from hush.providers.ops.chain import extract

    mock_hub, call_count = make_mock_hub([
        "<intent>DENY</intent><confidence>0.95</confidence>",
    ])

    with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub

        with GraphOp(name="test_workflow") as g:
            e = extract(
                resource="mock",
                template="Analyze: {text}",
                fields=["intent: str", "confidence: float"],
                parser="xml",
                text="không",
            )
            START >> e >> END

        engine = Hush(g)
        result = await engine.run(inputs={})

    assert result["intent"] == "DENY"
    assert result["confidence"] == 0.95
    print(f"  multi fields test: intent={result['intent']}, confidence={result['confidence']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
