"""OpenAI-shaped backends send only the message keys Chat Completions defines.

``operonx.agents`` keeps bookkeeping on its messages: an ``id`` on every
one (``add_messages`` upserts on it), and ``name``/``status`` on a tool
result. The Chat Completions schema has none of those on a tool message
and no ``id`` anywhere, and a strict gateway rejects the unknown
property — so an agent's second request, the first one carrying a tool
result, failed there. The backends strip them before sending, the way
they already strip a message-level ``cache_control``; the agent layer
keeps its keys.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from operonx.agents.graphs.dispatch import tool_message
from operonx.agents.ops.model_ops import adapt_llm_output
from operonx.providers.llms.base import BaseLLM, openai_message

pytestmark = pytest.mark.unit

CALL = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "get_weather", "arguments": '{"city": "Hanoi"}'},
}


def _agent_conversation() -> list:
    """The messages a ReAct turn actually hands the backend."""
    assistant = adapt_llm_output.__wrapped__(content="", tool_calls=[CALL])["assistant_message"][0]
    return [
        {"id": "sys", "role": "system", "content": "Be brief.", "cache_control": {"type": "x"}},
        {"id": "u1", "role": "user", "content": "Weather in Hanoi?"},
        assistant,
        tool_message("call_1", "get_weather", "31C"),
    ]


ALLOWED = {
    "system": {"role", "content", "name"},
    "developer": {"role", "content", "name"},
    "user": {"role", "content", "name"},
    "assistant": {"role", "content", "name", "tool_calls", "refusal", "audio", "function_call"},
    "tool": {"role", "content", "tool_call_id"},
}


def _assert_standard(messages: list) -> None:
    for message in messages:
        extra = set(message) - ALLOWED[message["role"]]
        assert not extra, f"{message['role']} message carries {sorted(extra)}"


def test_the_agent_messages_carry_the_keys_in_question():
    """Guard the premise: if these vanish upstream the test below proves nothing."""
    conversation = _agent_conversation()
    assert "id" in conversation[2]
    assert {"name", "status"} <= set(conversation[3])


def test_base_prepare_params_sends_only_standard_keys():
    params = BaseLLM._prepare_params(
        SimpleNamespace(resolve_image_paths=lambda m: m),
        model="m",
        messages=_agent_conversation(),
        stream=False,
    )
    _assert_standard(params["messages"])
    tool = params["messages"][3]
    assert tool == {"role": "tool", "tool_call_id": "call_1", "content": "31C"}
    assert params["messages"][2]["tool_calls"] == [CALL]


def test_azure_prepare_params_sends_only_standard_keys():
    from operonx.providers.llms.azure import AzureSDKModel
    from operonx.providers.llms.config import AzureConfig

    llm = AzureSDKModel(
        AzureConfig(
            api_key="k",
            api_version="2024-06-01",
            azure_endpoint="https://example.invalid",
            model="gpt",
        )
    )
    params = llm._prepare_params(model="gpt", messages=_agent_conversation(), stream=False)
    _assert_standard(params["messages"])


@pytest.mark.asyncio
async def test_openai_batch_sends_only_standard_keys(monkeypatch):
    from operonx.providers.llms.config import OpenAIConfig
    from operonx.providers.llms.openai import OpenAISDKModel

    llm = OpenAISDKModel(OpenAIConfig(api_key="k", base_url="https://example.invalid", model="m"))
    sent = []

    async def fake_create(requests):
        sent.extend(requests)
        return {"id": "batch_1"}

    monkeypatch.setattr(llm, "batch_create", fake_create)
    await llm.submit_batch([_agent_conversation()])
    _assert_standard(sent[0]["body"]["messages"])


def test_the_callers_messages_are_not_mutated():
    conversation = _agent_conversation()
    for message in conversation:
        openai_message(message)
    assert "id" in conversation[2]
    assert "status" in conversation[3]


def test_an_unknown_role_passes_through():
    """Not ours to judge — a provider-specific role is the caller's call."""
    message = {"role": "developer", "content": "x", "id": "d"}
    assert openai_message(message) == {"role": "developer", "content": "x"}
    odd = {"role": "ipython", "content": "x", "whatever": 1}
    assert openai_message(odd) == odd
