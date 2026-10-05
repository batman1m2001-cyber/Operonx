"""``operonx_agents.testing``: what a project's own tests use to run an
agent offline."""

from __future__ import annotations

from operonx.core.registry.resource_hub import ResourceHub

from operonx_agents import Agent, Model, Runner, tool
from operonx_agents.testing import ScriptedLLM, asks, says, scripted


@tool(readonly=True)
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


async def test_a_scripted_run_and_the_hub_is_put_back():
    before = scripted(other=ScriptedLLM(says("x")))
    outer = before.__enter__()
    llm = ScriptedLLM(asks(("add", {"a": 2, "b": 3})), says("5"))
    with scripted(assistant=llm):
        res = await Runner.run(Agent(name="calc", model=Model("assistant"), tools=[add]), "2+3?")
    assert (res.status, res.output, llm.calls) == ("completed", "5", 2)
    (said,) = [m for m in res.messages if m["role"] == "tool"]
    assert said["content"] == "5"
    assert ResourceHub.instance() is outer
    before.__exit__(None, None, None)


async def test_a_rule_answers_from_the_conversation():
    def rule(messages, params):
        return says(f"you said {messages[-1]['content']}")

    with scripted(m=ScriptedLLM(rule)):
        res = await Runner.run(Agent(name="echo", model=Model("m")), "hi")
    assert res.output == "you said hi"


async def test_scripted_layers_over_the_hub_so_tools_keep_real_resources(tmp_path):
    from operonx.core.registry.resource_hub import ResourceHub as Hub

    path = tmp_path / "resources.yaml"
    path.write_text("llm:real:\n  api_type: fake\n  script: []\n", encoding="utf-8")
    real = Hub.from_yaml(path)
    Hub.set_instance(real)
    try:
        with scripted(assistant=ScriptedLLM(says("x"))) as hub:
            assert hub.get("llm:real") is real.get("llm:real")  # not scripted: from the hub below
            assert hub.get("llm:assistant") is hub.llms["assistant"]
        assert Hub.instance() is real
    finally:
        Hub.reset_instance()
