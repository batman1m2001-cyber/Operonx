"""Runtime tests for LLMOp structured mode + semantic retry (replaces the
old ask() / ask(until="error==None") test surface).

The old suite exercised ``ask()`` with and without ``@graph(until=...)``.
Since 1.0.0 the retry logic lives inside LLMOp itself and uses no loop
primitive — the tests below drive that path directly.
"""

from unittest.mock import Mock, patch

import pytest

from operonx.core import END, START, Operon, graph
from operonx.providers.ops import LLMOp

# ---------------------------------------------------------------------------
# Mock hub — LLM returns a scripted sequence of responses.
# ---------------------------------------------------------------------------


def make_mock_hub(responses, finish_reasons=None, refusals=None):
    """Mock ResourceHub whose LLM returns ``responses`` in order.

    ``finish_reasons`` / ``refusals`` are optional per-index overrides for
    exercising refusal detection.
    """
    call_count = {"n": 0, "messages_history": []}

    async def mock_generate(messages, **kwargs):
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        call_count["messages_history"].append(list(messages))
        content = responses[idx]
        finish_reason = (
            finish_reasons[idx] if finish_reasons and idx < len(finish_reasons) else "stop"
        )
        refusal = refusals[idx] if refusals and idx < len(refusals) else None

        from openai.types.chat.chat_completion import ChatCompletion, Choice
        from openai.types.chat.chat_completion_message import ChatCompletionMessage
        from openai.types.completion_usage import CompletionUsage

        msg = ChatCompletionMessage(role="assistant", content=content)
        if refusal is not None:
            msg.refusal = refusal
        return ChatCompletion(
            id=f"mock-{idx}",
            created=1000000,
            model="mock-model",
            object="chat.completion",
            choices=[Choice(index=0, message=msg, finish_reason=finish_reason)],
            usage=CompletionUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    mock_llm = Mock()
    mock_llm.generate = mock_generate

    mock_hub = Mock()
    mock_hub.get.return_value = mock_llm
    return mock_hub, call_count


def _wf(**llm_kwargs):
    """Build a minimal graph containing ONE ``LLMOp.of(**llm_kwargs)``.

    The op must be created inside the ``with GraphOp`` context so
    auto-registration wires it into ``g._ops``. Creating outside first and
    referencing by name later would fail because the op instance never
    registered with any parent graph.
    """
    from operonx.core.ops.graph.graph_op import GraphOp

    with GraphOp(name="wf") as g:
        node = LLMOp.of(**llm_kwargs)
        START >> node >> END
    return g


# =============================================================================
# Structured mode — no retry
# =============================================================================


@pytest.mark.asyncio
async def test_structured_basic():
    """LLMOp with fields= parses inline; no retry needed on success."""
    mock_hub, call_count = make_mock_hub(["<intent>CONFIRM</intent>"])
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        g = _wf(
            resource="mock",
            prompt="Classify: {text}",
            fields=["intent: str"],
            parser="xml",
            text="vâng đúng rồi",
        )
        result = await Operon(g).run(inputs={})

    assert call_count["n"] == 1
    assert result["intent"] == "CONFIRM"
    assert result["error"] is None


@pytest.mark.asyncio
async def test_validators_default_applies_on_mismatch():
    """@-prefixed validator entry acts as a default when extracted value
    isn't in the allow list — no retry needed."""
    mock_hub, call_count = make_mock_hub(["<result>UNKNOWN</result>"])
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        g = _wf(
            resource="mock",
            prompt="Classify: {text}",
            fields=["result: str"],
            parser="xml",
            validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
            text="test",
        )
        result = await Operon(g).run(inputs={})

    assert call_count["n"] == 1
    assert result["result"] == "FALLBACK"
    assert result["error"] is None


@pytest.mark.asyncio
async def test_multiple_fields():
    mock_hub, _ = make_mock_hub(["<intent>DENY</intent><confidence>0.95</confidence>"])
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        g = _wf(
            resource="mock",
            prompt="Analyze: {text}",
            fields=["intent: str", "confidence: float"],
            parser="xml",
            text="không",
        )
        result = await Operon(g).run(inputs={})

    assert result["intent"] == "DENY"
    assert result["confidence"] == 0.95


# =============================================================================
# Semantic retry — error-guided, same resource
# =============================================================================


@pytest.mark.asyncio
async def test_semantic_retry_parse_then_success():
    """First LLM call returns garbage → retry with hint → second succeeds."""
    mock_hub, call_count = make_mock_hub(
        [
            "<unclosed>",
            "<result>CONFIRM</result>",
        ]
    )
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        g = _wf(
            resource="mock",
            prompt="Classify: {text}",
            fields=["result: str"],
            parser="xml",
            validators={"result": ["CONFIRM", "DENY"]},
            max_retries=2,
            text="vâng đúng rồi",
        )
        result = await Operon(g).run(inputs={})

    assert call_count["n"] == 2
    assert result["result"] == "CONFIRM"
    assert result["error"] is None


@pytest.mark.asyncio
async def test_semantic_retry_injects_prior_error_as_hint():
    """When retry_hint=True (default), the retry attempt receives the last
    bad response plus a user turn describing the failure."""
    mock_hub, call_count = make_mock_hub(["<unclosed>", "<result>CONFIRM</result>"])
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        g = _wf(
            resource="mock",
            prompt="Classify: {text}",
            fields=["result: str"],
            parser="xml",
            max_retries=1,
            text="x",
        )
        await Operon(g).run(inputs={})

    # First attempt: just the original messages.
    assert len(call_count["messages_history"][0]) == 1
    # Second attempt: original + assistant echo + retry-hint user turn.
    second = call_count["messages_history"][1]
    assert len(second) == 3
    assert second[1]["role"] == "assistant"
    assert second[1]["content"] == "<unclosed>"
    assert second[2]["role"] == "user"
    assert "failed" in second[2]["content"].lower()


@pytest.mark.asyncio
async def test_retry_hint_off_does_not_inject_messages():
    """retry_hint=False → retry attempts see the original messages only."""
    mock_hub, call_count = make_mock_hub(["<unclosed>", "<result>CONFIRM</result>"])
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        g = _wf(
            resource="mock",
            prompt="Classify: {text}",
            fields=["result: str"],
            parser="xml",
            max_retries=1,
            retry_hint=False,
            text="x",
        )
        await Operon(g).run(inputs={})

    # Every attempt has the same message count as the first.
    for msgs in call_count["messages_history"]:
        assert len(msgs) == 1


@pytest.mark.asyncio
async def test_max_retries_zero_makes_single_attempt():
    """max_retries=0 (default) → exactly one LLM call; parse failure surfaces
    via the ``error`` output field."""
    mock_hub, call_count = make_mock_hub(["<unclosed>"])
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        g = _wf(
            resource="mock",
            prompt="Classify: {text}",
            fields=["result: str"],
            parser="xml",
            validators={"result": ["CONFIRM", "DENY"]},
            text="test",
        )
        result = await Operon(g).run(inputs={})

    assert call_count["n"] == 1
    assert result["result"] is None
    assert result["error"] is not None


@pytest.mark.asyncio
async def test_max_retries_exhausted_surfaces_error():
    """LLM never produces valid output → after max_retries+1 attempts,
    return with ``error`` set and every field=None."""
    mock_hub, call_count = make_mock_hub(["<unclosed>"] * 5)
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        g = _wf(
            resource="mock",
            prompt="Classify: {text}",
            fields=["result: str"],
            parser="xml",
            validators={"result": ["CONFIRM", "DENY"]},
            max_retries=2,
            text="test",
        )
        result = await Operon(g).run(inputs={})

    assert call_count["n"] == 3
    assert result["result"] is None
    assert result["error"] is not None
    assert "Parse error" in result["error"] or "Validation" in result["error"]


@pytest.mark.asyncio
async def test_first_try_success_no_retries():
    mock_hub, call_count = make_mock_hub(["<intent>CONFIRM</intent>"])
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        g = _wf(
            resource="mock",
            prompt="Classify: {text}",
            fields=["intent: str"],
            parser="xml",
            validators={"intent": ["CONFIRM", "DENY"]},
            max_retries=3,
            text="vâng",
        )
        result = await Operon(g).run(inputs={})

    assert call_count["n"] == 1
    assert result["intent"] == "CONFIRM"


@pytest.mark.asyncio
async def test_retry_twice_then_success():
    mock_hub, call_count = make_mock_hub(
        [
            "<unclosed>",
            "<result>UNKNOWN</result>",  # parses but validator rejects
            "<result>CONFIRM</result>",
        ]
    )
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        g = _wf(
            resource="mock",
            prompt="Classify: {text}",
            fields=["result: str"],
            parser="xml",
            validators={"result": ["CONFIRM", "DENY"]},
            max_retries=3,
            text="vâng đúng rồi",
        )
        result = await Operon(g).run(inputs={})

    assert call_count["n"] == 3
    assert result["result"] == "CONFIRM"
    assert result["error"] is None


# =============================================================================
# Refusal handling — fallback triggers on refusal, not on semantic failure
# =============================================================================


@pytest.mark.asyncio
async def test_refusal_via_finish_reason_no_fallback_records_error():
    """Primary responds with finish_reason='content_filter' and no fallback
    configured → LLMOp raises LLMRefusalError → the scheduler records it on
    the op's ``error`` state cell (its usual pattern for op exceptions)."""
    mock_hub, _ = make_mock_hub(
        ["I can't help with that."],
        finish_reasons=["content_filter"],
    )
    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        g = _wf(resource="mock", prompt="X")
        # Scheduler catches the exception and stores it on state; run
        # completes but the op's ``error`` cell holds the refusal message.
        result = await Operon(g).run(inputs={})

    state = result["$state"]
    err_idx = state.schema.get_index(g.full_name + ".node", "error")
    # The op is named "node" via auto-name inside _wf() (the local var name).
    # Fall back to scanning if the auto-name resolves differently.
    if err_idx < 0:
        for (op, var), idx in state.schema._var_to_idx.items():
            if var == "error" and "error" not in op:
                err_idx = idx
                break
    assert err_idx >= 0
    err_val = state._cells[err_idx][("main",)]
    assert err_val is not None
    assert "refused" in str(err_val).lower()


@pytest.mark.asyncio
async def test_semantic_failure_does_not_trigger_fallback():
    """Parse failure must not consume the fallback budget — different
    LLM won't fix a parser-shape bug. The mock keeps returning bad output;
    LLMOp should retry on SAME resource, never call fallback."""
    fallback_called = {"n": 0}

    async def fallback_generate(**kwargs):
        fallback_called["n"] += 1
        raise AssertionError("fallback should not be called on semantic failure")

    mock_hub, call_count = make_mock_hub(["<unclosed>", "<unclosed>"])
    fallback_llm = Mock()
    fallback_llm.generate = fallback_generate

    def get_side_effect(key):
        if key == "fallback-model":
            return fallback_llm
        return mock_hub.get.return_value

    mock_hub.get.side_effect = get_side_effect

    with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
        mock_cls.instance.return_value = mock_hub
        g = _wf(
            resource="mock",
            fallback=["fallback-model"],
            prompt="Classify: {text}",
            fields=["result: str"],
            parser="xml",
            max_retries=1,
            text="x",
        )
        result = await Operon(g).run(inputs={})

    assert call_count["n"] == 2  # primary retried once
    assert fallback_called["n"] == 0  # fallback never touched
    assert result["error"] is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
