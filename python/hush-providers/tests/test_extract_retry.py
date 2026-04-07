"""Tests for ask() and ask(until="error == None", error="init", ).

- ask(): simple Prompt → LLM → Parser (no retry)
- ask(until="error == None", error="init", ): with retry loop via GraphOp.loop
"""

from unittest.mock import Mock, patch

import pytest
from hush.core import END, START, GraphOp, Hush, graph

# ── Helper: mock LLM that returns different content each call ────────


def make_mock_hub(responses):
    """Create a mock ResourceHub where LLM returns responses in order."""
    call_count = {"n": 0}

    async def mock_generate(messages, **kwargs):
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        content = responses[idx]

        from openai.types.chat.chat_completion import ChatCompletion, Choice
        from openai.types.chat.chat_completion_message import ChatCompletionMessage
        from openai.types.completion_usage import CompletionUsage

        return ChatCompletion(
            id=f"mock-{idx}",
            created=1000000,
            model="mock-model",
            object="chat.completion",
            choices=[
                Choice(
                    index=0,
                    message=ChatCompletionMessage(role="assistant", content=content),
                    finish_reason="stop",
                )
            ],
            usage=CompletionUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    mock_llm = Mock()
    mock_llm.generate = mock_generate

    mock_hub = Mock()
    mock_hub.get.return_value = mock_llm

    return mock_hub, call_count


# =============================================================================
# ask() tests — simple, no retry
# =============================================================================


@pytest.mark.asyncio
async def test_extract_basic():
    """ask() parses LLM output into structured fields."""
    from hush.providers.ops.chain import ask

    mock_hub, call_count = make_mock_hub(["<intent>CONFIRM</intent>"])

    with patch("hush.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub

        @graph
        def wf():
            e = ask(
                resource="mock",
                template="Classify: {text}",
                fields=["intent: str"],
                parser="xml",
                text="vâng đúng rồi",
            )
            START >> e >> END

        engine = Hush(wf())
        result = await engine.run(inputs={})

    assert call_count["n"] == 1
    assert result["intent"] == "CONFIRM"
    print(f"  extract basic: intent={result['intent']}")


@pytest.mark.asyncio
async def test_extract_with_validators():
    """ask() with validators — @-prefixed = default on validation fail."""
    from hush.providers.ops.chain import ask

    mock_hub, call_count = make_mock_hub(["<result>UNKNOWN</result>"])

    with patch("hush.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub

        @graph
        def wf():
            e = ask(
                resource="mock",
                template="Classify: {text}",
                fields=["result: str"],
                parser="xml",
                validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
                text="test",
            )
            START >> e >> END

        engine = Hush(wf())
        result = await engine.run(inputs={})

    assert call_count["n"] == 1
    assert result["result"] == "FALLBACK"  # @-default applied
    print(f"  extract with validators: result={result['result']}")


@pytest.mark.asyncio
async def test_extract_multiple_fields():
    """ask() extracts multiple fields from XML."""
    from hush.providers.ops.chain import ask

    mock_hub, call_count = make_mock_hub(["<intent>DENY</intent><confidence>0.95</confidence>"])

    with patch("hush.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub

        @graph
        def wf():
            e = ask(
                resource="mock",
                template="Analyze: {text}",
                fields=["intent: str", "confidence: float"],
                parser="xml",
                text="không",
            )
            START >> e >> END

        engine = Hush(wf())
        result = await engine.run(inputs={})

    assert result["intent"] == "DENY"
    assert result["confidence"] == 0.95
    print(f"  multi fields: intent={result['intent']}, confidence={result['confidence']}")


# =============================================================================
# ask(until="error == None", error="init", ) tests — with retry loop
# =============================================================================


@pytest.mark.asyncio
async def test_extract_retry_validator_reject_uses_default():
    """LLM always returns wrong value → validator rejects → @-default applied."""
    from hush.providers.ops.chain import ask

    mock_hub, call_count = make_mock_hub(["<result>UNKNOWN</result>"] * 3)

    with patch("hush.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub

        with GraphOp(name="test_workflow") as g:
            e = ask(
                until="error == None",
                error="init",
                resource="mock",
                template="Classify: {text}",
                fields=["result: str"],
                parser="xml",
                max_iterations=3,
                validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
                text="some input",
            )
            START >> e >> END

        engine = Hush(g)
        result = await engine.run(inputs={})

    # @FALLBACK → ParserOp uses default immediately, no retry needed
    assert call_count["n"] == 1
    assert result["result"] == "FALLBACK"
    assert result["error"] is None
    print(f"  retry validator reject: result={result['result']}, calls={call_count['n']}")


@pytest.mark.asyncio
async def test_extract_retry_parse_fail_then_success():
    """First call returns garbage → retry → second call returns valid XML."""
    from hush.providers.ops.chain import ask

    mock_hub, call_count = make_mock_hub(
        [
            "this is not xml at all!!!",
            "<result>CONFIRM</result>",
        ]
    )

    with patch("hush.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub

        with GraphOp(name="test_workflow") as g:
            e = ask(
                until="error == None",
                error="init",
                resource="mock",
                template="Classify: {text}",
                fields=["result: str"],
                parser="xml",
                max_iterations=2,
                validators={"result": ["CONFIRM", "DENY"]},
                text="vâng đúng rồi",
            )
            START >> e >> END

        engine = Hush(g)
        result = await engine.run(inputs={})

    assert call_count["n"] == 2
    assert result["result"] == "CONFIRM"
    print(f"  retry parse fail: result={result['result']}, calls={call_count['n']}")


@pytest.mark.asyncio
async def test_extract_retry_no_retry_default_on_fail():
    """retry=0 → only 1 attempt. Parse fail → @-default applied."""
    from hush.providers.ops.chain import ask

    mock_hub, call_count = make_mock_hub(["garbage output"])

    with patch("hush.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub

        with GraphOp(name="test_workflow") as g:
            e = ask(
                until="error == None",
                error="init",
                resource="mock",
                template="Classify: {text}",
                fields=["result: str"],
                parser="xml",
                max_iterations=1,
                validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
                text="test",
            )
            START >> e >> END

        engine = Hush(g)
        result = await engine.run(inputs={})

    assert call_count["n"] == 1
    assert result["result"] == "FALLBACK"
    print(f"  no retry: result={result['result']}, calls={call_count['n']}")


@pytest.mark.asyncio
async def test_extract_retry_success_first_try():
    """LLM returns valid output on first try → no retry triggered."""
    from hush.providers.ops.chain import ask

    mock_hub, call_count = make_mock_hub(["<intent>CONFIRM</intent>"])

    with patch("hush.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub

        with GraphOp(name="test_workflow") as g:
            e = ask(
                until="error == None",
                error="init",
                resource="mock",
                template="Classify: {text}",
                fields=["intent: str"],
                parser="xml",
                max_iterations=3,
                validators={"intent": ["CONFIRM", "DENY"]},
                text="vâng",
            )
            START >> e >> END

        engine = Hush(g)
        result = await engine.run(inputs={})

    assert call_count["n"] == 1
    assert result["intent"] == "CONFIRM"
    print(f"  first try success: intent={result['intent']}, calls={call_count['n']}")


@pytest.mark.asyncio
async def test_extract_retry_twice_then_success():
    """LLM fails 2 times, succeeds on 3rd attempt."""
    from hush.providers.ops.chain import ask

    mock_hub, call_count = make_mock_hub(
        [
            "not xml at all",
            "<result>UNKNOWN</result>",
            "<result>CONFIRM</result>",
        ]
    )

    with patch("hush.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub

        with GraphOp(name="test_workflow") as g:
            e = ask(
                until="error == None",
                error="init",
                resource="mock",
                template="Classify: {text}",
                fields=["result: str"],
                parser="xml",
                max_iterations=3,
                validators={"result": ["CONFIRM", "DENY"]},
                text="vâng đúng rồi",
            )
            START >> e >> END

        engine = Hush(g)
        result = await engine.run(inputs={})

    assert call_count["n"] == 3
    assert result["result"] == "CONFIRM"
    assert result["error"] is None
    print(f"  retry twice: result={result['result']}, calls={call_count['n']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
