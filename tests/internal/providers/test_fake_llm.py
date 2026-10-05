"""``api_type: fake`` (DX_PLAN X1): a scripted LLM an LLMOp runs on with no
network — text, a stream, tool calls, a provider error, in script order."""

from __future__ import annotations

import asyncio

import openai
import pytest

from operonx import END, START, Operon, graph
from operonx.core.registry import ResourceHub
from operonx.providers.ops import LLMOp

pytestmark = pytest.mark.unit

RESOURCES = """
llm:bot:
  api_type: fake
  chunk_size: 3
  script:
    - "Hello there"
    - tool_calls: [{name: lookup, args: {order: 7}}]
    - {status: 429}
    - {delay: 0.01, text: "late"}
"""


@pytest.fixture
def hub(tmp_path):
    path = tmp_path / "resources.yaml"
    path.write_text(RESOURCES)
    hub = ResourceHub.from_yaml(path)
    saved = ResourceHub._instance
    ResourceHub.set_instance(hub)
    try:
        yield hub
    finally:
        ResourceHub._instance = saved


@graph
def ask(q):
    llm = LLMOp.of(resource="bot", prompt="{q}", q=q)
    START >> llm >> END


def test_turns_come_in_script_order_and_the_last_repeats(hub):
    engine = Operon(ask, params={"q": None})

    async def go():
        return [await engine.run({"q": f"q{i}"}) for i in range(5)]

    first, second, third, fourth, fifth = asyncio.run(go())
    assert first["content"] == "Hello there" and first["finish_reason"] == "stop"
    assert second["tool_calls"][0]["name"] == "lookup"
    assert second["tool_calls"][0]["args"] == {"order": 7}
    assert "RateLimitError" in str(third["$errors"])  # what a real 429 raises
    assert fourth["content"] == fifth["content"] == "late"
    assert [m[-1]["content"] for m in hub.get("llm:bot").calls] == [f"q{i}" for i in range(5)]


def test_a_stream_arrives_in_chunks(hub):
    llm = hub.get("llm:bot")

    async def go():
        parts = []
        async for chunk in llm.stream([{"role": "user", "content": "hi"}]):
            if chunk.choices and chunk.choices[0].delta.content:
                parts.append(chunk.choices[0].delta.content)
        return parts

    assert asyncio.run(go()) == ["Hel", "lo ", "the", "re"]


def test_a_scripted_status_is_the_sdk_error(hub):
    llm = hub.get("llm:bot")
    llm._next = 2

    with pytest.raises(openai.RateLimitError):
        asyncio.run(llm.generate([{"role": "user", "content": "x"}]))


def test_a_turn_it_cannot_read_is_refused(tmp_path):
    path = tmp_path / "resources.yaml"
    path.write_text("llm:bad:\n  api_type: fake\n  script: [{colour: red}]\n")
    with pytest.raises(KeyError, match="colour"):
        ResourceHub.from_yaml(path).get("llm:bad")


def test_an_echo_turn_answers_with_the_last_user_message(tmp_path):
    path = tmp_path / "resources.yaml"
    path.write_text('llm:e:\n  api_type: fake\n  script: [{echo: "Echo: "}]\n')
    llm = ResourceHub.from_yaml(path).get("llm:e")
    msgs = [{"role": "user", "content": "first"}, {"role": "user", "content": "second"}]
    out = asyncio.run(llm.generate(msgs))
    assert out.choices[0].message.content == "Echo: second"
