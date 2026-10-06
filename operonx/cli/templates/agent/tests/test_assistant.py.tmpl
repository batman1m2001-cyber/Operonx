"""The assistant on a scripted model: offline, and the same every run."""

import asyncio

from operonx.agents import Runner
from operonx.agents.testing import ScriptedLLM, asks, says, scripted
from operonx.app.serve.app import build_app
from starlette.testclient import TestClient

from app.main import APP
from assistant.graph import ASSISTANT
from assistant.ops import word_count


def test_word_count():
    assert word_count("one two three") == 3  # a tool is a function: call it


def test_the_agent_calls_the_tool_and_answers():
    model = ScriptedLLM(asks(("word_count", {"text": "one two three"})), says("3 words."))
    with scripted(assistant=model):  # llm:assistant answers from the script
        res = asyncio.run(Runner.run(ASSISTANT, "How many words in 'one two three'?"))
    assert (res.status, res.output) == ("completed", "3 words.")
    (tool_said,) = [m["content"] for m in res.messages if m["role"] == "tool"]
    assert tool_said == "3"


def test_the_service_answers_over_http():
    with scripted(assistant=ScriptedLLM(says("Hello!"))):
        with TestClient(build_app(APP.services)) as client:
            reply = client.post("/ask", json={"input": "hi"}).json()
    assert reply["status"] == "completed" and reply["output"] == "Hello!"
