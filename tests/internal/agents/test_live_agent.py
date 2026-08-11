"""End-to-end against a real tool-calling model.

Everything else in this directory scripts the model. That verifies the
loop, dispatch, budget and approval — but not the seam between `LLMOp`
and `build_react_agent`, which is exactly where the interesting failures
were. Three bugs got through the entire unit suite and only appeared
here:

1. `LLMOp` and its adapter landed on opposite sides of the synthesized
   loop, so the graph would not build.
2. A subgraph emits `None` for outputs a frame did not write, which the
   `add_messages` reducer rejects — the run ended quietly mid-conversation.
3. **`LLMOp` templates its prompt.** A tool returning `{"city": "Hanoi"}`
   made the next call raise `PromptError: Missing template variable(s)`.
   Every scripted model in the suite returns brace-free prose, so nothing
   else could have found it.

Marked `integration`: needs credentials and costs money, so it is
excluded by the default `-m "not integration"` selector. Run it before
shipping a change to the model seam.

    export OPERONX_TEST_LLM_URL=... OPERONX_TEST_LLM_KEY=... OPERONX_TEST_LLM_MODEL=...
    uv run pytest tests/internal/agents/test_live_agent.py -m integration

**The provider must support tool calling.** Many OpenAI-compatible
gateways accept `tools` and answer in prose anyway — vLLM needs
`--enable-auto-tool-choice`. `test_provider_supports_tool_calling` checks
that first so a gateway misconfiguration reads as itself rather than as
an agent bug.
"""

from __future__ import annotations

import asyncio
import os
import textwrap

import pytest

from operonx.agents import (
    agent_result,
    build_react_agent,
    clear_registry,
    get_tool_definitions,
    tool,
)
from operonx.agents.ops.model_ops import make_llm_caller
from operonx.core import Operon
from operonx.core.registry import ResourceHub

pytestmark = pytest.mark.integration

URL = os.getenv("OPERONX_TEST_LLM_URL")
KEY = os.getenv("OPERONX_TEST_LLM_KEY", "")
MODEL = os.getenv("OPERONX_TEST_LLM_MODEL", "")

requires_llm = pytest.mark.skipif(
    not (URL and MODEL),
    reason="set OPERONX_TEST_LLM_URL / _KEY / _MODEL to run live agent tests",
)

RESOURCE = "livetest"


@pytest.fixture(scope="module", autouse=True)
def _hub(tmp_path_factory):
    if not (URL and MODEL):
        yield
        return
    path = tmp_path_factory.mktemp("live") / "resources.yaml"
    path.write_text(
        textwrap.dedent(f"""
            llm:
              {RESOURCE}:
                api_type: openai
                api_key: {KEY or "unused"}
                base_url: {URL}
                model: {MODEL}
        """).strip(),
        encoding="utf-8",
    )
    # `instance()` raises rather than returning None when unset, so this
    # cannot be a truthiness check.
    try:
        previous = ResourceHub.instance()
    except RuntimeError:
        previous = None

    ResourceHub.set_instance(ResourceHub.from_yaml(str(path)))
    yield
    if previous is not None:
        ResourceHub.set_instance(previous)


@pytest.fixture
def weather_tool():
    clear_registry()
    calls = []

    @tool(
        name="get_weather",
        description="Get the current weather for a city.",
        schema={
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name."}},
            "required": ["city"],
        },
        readonly=True,
    )
    async def get_weather(city: str) -> dict:
        calls.append(city)
        # JSON in the result is the point: it is what broke prompt
        # templating, and a realistic tool returns structured data.
        return {"city": city, "temp_c": 21, "sky": "clear"}

    yield calls
    clear_registry()


async def run_agent(prompt: str, *, max_turns: int = 5, tools=None):
    caller = make_llm_caller(
        RESOURCE,
        tools=tools if tools is not None else get_tool_definitions(),
        max_tokens=512,
    )
    agent = build_react_agent(call_model=caller, max_turns=max_turns)(messages=None)
    result = await asyncio.wait_for(
        Operon(agent).run(inputs={"messages": [{"role": "user", "content": prompt}]}),
        timeout=180,
    )
    return agent_result(result, agent)


@requires_llm
@pytest.mark.asyncio
async def test_provider_supports_tool_calling(weather_tool):
    """Checked first so a gateway that ignores `tools` reads as a
    misconfigured server rather than as an agent bug."""
    answer = await run_agent("What is the weather in Hanoi? Use the tool.")
    assert weather_tool == ["Hanoi"], (
        "the model never called the tool — the gateway probably does not "
        "support tool calling (vLLM needs --enable-auto-tool-choice)"
    )


@requires_llm
@pytest.mark.asyncio
async def test_full_loop_reaches_a_final_answer(weather_tool):
    answer = await run_agent("What is the weather in Hanoi? Use the tool.")

    assert [m["role"] for m in answer["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ], "expected exactly one tool round trip then an answer"
    assert answer["turns"] == 2
    assert answer["stopped_early"] is False
    assert answer["final"]["role"] == "assistant"
    assert answer["final"]["content"].strip(), "the final turn must carry text"


@requires_llm
@pytest.mark.asyncio
async def test_tool_result_json_does_not_break_the_next_call(weather_tool):
    """Regression for the prompt-templating collision.

    The tool returns `{"city": ...}`; `LLMOp` runs `format_map` over its
    prompt, so without escaping the second call raises
    `PromptError: Missing template variable(s) '"city"'` and the agent
    stops one turn short with no answer.
    """
    answer = await run_agent("What is the weather in Hanoi? Use the tool.")
    tool_messages = [m for m in answer["messages"] if m.get("role") == "tool"]
    assert tool_messages and "{" in tool_messages[0]["content"]
    assert answer["final"] is not None, "the model never got to answer"


@requires_llm
@pytest.mark.asyncio
async def test_assistant_turn_keeps_its_tool_calls(weather_tool):
    """A tool-calling turn stripped of its calls leaves the tool messages
    answering nothing, and the provider rejects the conversation."""
    answer = await run_agent("What is the weather in Hanoi? Use the tool.")
    requesting = [m for m in answer["messages"] if m.get("tool_calls")]
    assert requesting, "the assistant turn must carry the calls it made"

    from operonx.agents import unmatched_tool_calls

    assert unmatched_tool_calls(answer["messages"]) == {
        "calls_without_results": [],
        "results_without_calls": [],
    }


@requires_llm
@pytest.mark.asyncio
async def test_no_tools_still_answers():
    """The degenerate case: an agent with no tools is valid, not an
    error, and must not loop."""
    clear_registry()
    answer = await run_agent("Say the single word: pong.", tools=[])
    assert answer["turns"] == 1
    assert answer["final"]["content"].strip()
