"""End-to-end scenarios a scripted model cannot reach.

`test_live_agent.py` proves the seam between `LLMOp` and the loop works
at all. This file goes after the parts of the system that only a *real*
model exercises: multi-turn state, compaction firing mid-conversation,
parallel tool calls the model chose to make, sub-agents, structured
output, and content shaped to break the layers underneath.

Every scenario here exists because a scripted double cannot produce the
input that breaks it. A fake model returns the tool calls you told it to
return, in the order you chose, with brace-free prose. A real one decides
how many tools to call, returns JSON, writes `{` in a code block, and
occasionally answers in a way nobody planned for.

    export OPERONX_TEST_LLM_URL=... OPERONX_TEST_LLM_KEY=... OPERONX_TEST_LLM_MODEL=...
    uv run pytest tests/internal/agents/test_live_e2e.py -m integration
"""

from __future__ import annotations

import asyncio
import os
import textwrap

import pytest

from operonx.agents import (
    AgentSession,
    Redactor,
    ToolPolicy,
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
    reason="set OPERONX_TEST_LLM_URL / _KEY / _MODEL to run live e2e tests",
)

RESOURCE = "e2etest"


@pytest.fixture(scope="module", autouse=True)
def _hub(tmp_path_factory):
    if not (URL and MODEL):
        yield
        return
    path = tmp_path_factory.mktemp("e2e") / "resources.yaml"
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
    try:
        previous = ResourceHub.instance()
    except RuntimeError:
        previous = None
    ResourceHub.set_instance(ResourceHub.from_yaml(str(path)))
    yield
    if previous is not None:
        ResourceHub.set_instance(previous)


@pytest.fixture(autouse=True)
def _registry():
    clear_registry()
    yield
    clear_registry()


def _agent(*, max_turns=8, tools=None, **kw):
    caller = make_llm_caller(
        RESOURCE,
        tools=tools if tools is not None else get_tool_definitions(),
        max_tokens=768,
        temperature=0,
    )
    return build_react_agent(call_model=caller, max_turns=max_turns, **kw)(messages=None)


async def _run(agent, text, timeout=240):
    result = await asyncio.wait_for(
        Operon(agent).run(inputs={"messages": [{"role": "user", "content": text}]}),
        timeout=timeout,
    )
    return agent_result(result, agent)


# ── content that breaks the layers underneath ────────────────────────────


@requires_llm
@pytest.mark.asyncio
async def test_a_tool_returning_json_does_not_poison_the_next_turn():
    """The C6 regression, end to end. Braces in a tool result used to be
    read as template variables and killed the *following* model call —
    with `prompt=` gone from the loop this must stay dead."""

    @tool(
        name="fetch_config",
        description="Return the service configuration.",
        schema={"type": "object", "properties": {}},
        readonly=True,
    )
    async def fetch_config() -> dict:
        return {"raw": '{"db": {"host": "x"}, "flags": {"a": true}}'}

    result = await _run(_agent(), "Call fetch_config and tell me what the db host is.")
    assert result["final"]["role"] == "assistant"
    assert result["turns"] >= 2, "the model must have called the tool and come back"


@requires_llm
@pytest.mark.asyncio
async def test_a_tool_returning_code_with_braces_survives():
    """A coding agent reads files full of braces on every turn."""

    @tool(
        name="read_source",
        description="Return a source file.",
        schema={"type": "object", "properties": {}},
        readonly=True,
    )
    async def read_source() -> dict:
        return {"text": "def f(x):\n    return {'a': [1, 2], 'b': {'c': x}}\n"}

    result = await _run(_agent(), "Call read_source and say what f returns.")
    assert result["final"]["content"]


@requires_llm
@pytest.mark.asyncio
async def test_a_user_message_full_of_braces_is_not_a_template():
    """No tool involved — the user's own text goes through the same path."""
    result = await _run(
        _agent(tools=[]),
        "Explain this CSS in one sentence: body { margin: 0; padding: {a} }",
    )
    assert result["final"]["content"]


# ── multi-turn: the model decides what carries over ──────────────────────


@requires_llm
@pytest.mark.asyncio
async def test_a_session_carries_context_across_turns():
    """`AgentSession` threads the history. A model that cannot see turn 1
    will answer turn 2 wrongly rather than error, so this asserts on the
    answer's content."""
    session = AgentSession(_agent(tools=[]), timeout=240)
    await asyncio.wait_for(session.send("My favourite number is 8123. Remember it."), 240)
    result = await asyncio.wait_for(session.send("What is my favourite number?"), 240)
    assert "8123" in (result["final"] or {}).get("content", "")


@requires_llm
@pytest.mark.asyncio
async def test_a_tool_result_from_turn_one_is_visible_in_turn_two():
    """The history invariant across a session boundary, with tool
    messages in it — the shape a provider 400s on if it is malformed."""

    @tool(
        name="lookup_code",
        description="Look up the project's access code.",
        schema={"type": "object", "properties": {}},
        readonly=True,
    )
    async def lookup_code() -> dict:
        return {"code": "ZX-4471"}

    session = AgentSession(_agent(), timeout=240)
    await asyncio.wait_for(session.send("Use lookup_code to find the access code."), 240)
    result = await asyncio.wait_for(
        session.send("Repeat that code back to me, digits and all."), 240
    )
    assert "4471" in (result["final"] or {}).get("content", "")


@requires_llm
@pytest.mark.asyncio
async def test_compaction_mid_session_keeps_the_conversation_valid():
    """Compaction runs at prompt time on a real history containing real
    tool calls. If it drops a tool message whose tool_call is still
    referenced, the provider rejects the *next* request — so the failure
    lands a turn after the cause."""

    @tool(
        name="describe",
        description="Describe a thing at length.",
        schema={
            "type": "object",
            "properties": {"thing": {"type": "string"}},
            "required": ["thing"],
        },
        readonly=True,
    )
    async def describe(thing: str) -> dict:
        return {"text": f"{thing}: " + ("detail " * 400)}

    # A budget low enough that the second turn must compact.
    session = AgentSession(_agent(max_turns=6, token_budget=1500, keep_recent=2), timeout=300)
    await asyncio.wait_for(session.send("Describe a bicycle using the tool."), 300)
    await asyncio.wait_for(session.send("Now describe a kettle using the tool."), 300)
    result = await asyncio.wait_for(session.send("In one word, what did I ask about first?"), 300)

    assert (result["final"] or {}).get("role") == "assistant", (
        "a compacted history must still be a valid request"
    )


# ── the model decides how many tools to call ─────────────────────────────


@requires_llm
@pytest.mark.asyncio
async def test_parallel_tool_calls_all_get_answered():
    """Every tool_call needs a matching tool message or the provider 400s
    on the next turn. A scripted model emits the calls you chose; a real
    one decides, and may well fan out."""
    seen: list = []

    @tool(
        name="city_temp",
        description="Current temperature for one city.",
        schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
        readonly=True,
    )
    async def city_temp(city: str) -> dict:
        seen.append(city)
        return {"city": city, "temp_c": 20 + len(city) % 5}

    result = await _run(
        _agent(max_turns=8),
        "Use city_temp for Hanoi, Paris and Lima, then tell me which is warmest.",
    )
    tool_msgs = [m for m in result["messages"] if m.get("role") == "tool"]
    calls = [
        c
        for m in result["messages"]
        if m.get("role") == "assistant"
        for c in (m.get("tool_calls") or [])
    ]
    assert len(tool_msgs) == len(calls), (
        f"{len(calls)} tool calls but {len(tool_msgs)} results — "
        f"an unmatched tool_call makes the provider reject the next request"
    )
    assert len(seen) >= 2, f"expected several cities, got {seen}"


@requires_llm
@pytest.mark.asyncio
async def test_a_tool_that_raises_lets_the_model_recover():
    """A real model reads the error text and decides what to do. The
    scripted version only proves we produce *a* message."""
    attempts: list = []

    @tool(
        name="flaky_read",
        description="Read a record by id.",
        schema={
            "type": "object",
            "properties": {"record_id": {"type": "string"}},
            "required": ["record_id"],
        },
        readonly=True,
    )
    async def flaky_read(record_id: str) -> dict:
        attempts.append(record_id)
        if record_id != "R-100":
            raise ValueError(f"no such record {record_id!r}; the only valid id is 'R-100'")
        return {"record": "the answer is quartz"}

    result = await _run(
        _agent(max_turns=8),
        "Read record R-999 with flaky_read. If it fails, read whatever the error says exists.",
    )
    assert len(attempts) >= 2, f"the model never retried; attempts={attempts}"
    assert (result["final"] or {}).get("role") == "assistant"


# ── policy, approval and redaction under a real model ────────────────────


@requires_llm
@pytest.mark.asyncio
async def test_a_denied_destructive_call_does_not_run_and_the_agent_still_answers():
    deleted: list = []

    @tool(
        name="drop_table",
        description="Permanently delete a database table.",
        schema={
            "type": "object",
            "properties": {"table": {"type": "string"}},
            "required": ["table"],
        },
        destructive=True,
    )
    async def drop_table(table: str) -> dict:
        deleted.append(table)
        return {"dropped": table}

    session = AgentSession(_agent(max_turns=6, approval_timeout=30), timeout=240)

    def deny(event):
        session.approve(event, False, "denied by the test")

    result = await asyncio.wait_for(
        session.send("Drop the table named 'users' using drop_table.", on_approval=deny), 240
    )
    assert deleted == [], "a denied call must not execute"
    assert (result["final"] or {}).get("role") == "assistant"


@requires_llm
@pytest.mark.asyncio
async def test_a_deny_policy_never_reaches_the_tool():
    """`deny` differs from `ask`: no human is consulted at all."""
    ran: list = []

    @tool(
        name="wipe",
        description="Wipe everything.",
        schema={"type": "object", "properties": {}},
        destructive=True,
    )
    async def wipe() -> dict:
        ran.append(True)
        return {"ok": True}

    policy = ToolPolicy(default="allow", destructive="deny")
    result = await _run(_agent(max_turns=6, policy=policy), "Call wipe to clear everything.")
    assert ran == []
    assert (result["final"] or {}).get("role") == "assistant"


@requires_llm
@pytest.mark.asyncio
async def test_a_credential_in_a_tool_result_never_reaches_the_answer():
    """Redaction at the trust boundary, with a real model that would
    happily quote the key back."""

    @tool(
        name="read_env",
        description="Read the service environment file.",
        schema={"type": "object", "properties": {}},
        readonly=True,
    )
    async def read_env() -> dict:
        return {"env": 'OPENAI_API_KEY="sk-livetest-abcdefghijklmnop"\nREGION="eu"'}

    result = await _run(
        _agent(max_turns=6, redactor=Redactor()),
        "Call read_env and repeat its contents back to me exactly.",
    )
    blob = str(result["messages"])
    assert "sk-livetest-abcdefghijklmnop" not in blob, "the key reached the conversation"
    assert (result["final"] or {}).get("role") == "assistant"


# ── sub-agents ───────────────────────────────────────────────────────────


@requires_llm
@pytest.mark.asyncio
async def test_a_sub_agent_answers_without_leaking_its_transcript():
    """`make_delegate_tool` has never run against a live model. A child
    exists to spend context the parent need not hold, so the parent must
    get an answer and not a transcript."""
    from operonx.agents import make_delegate_tool

    @tool(
        name="secret_number",
        description="Return the project's secret number.",
        schema={"type": "object", "properties": {}},
        readonly=True,
    )
    async def secret_number() -> dict:
        return {"number": 6641}

    child_caller = make_llm_caller(
        RESOURCE, tools=get_tool_definitions(["secret_number"]), max_tokens=512, temperature=0
    )
    make_delegate_tool(call_model=child_caller, allow_tools=["secret_number"], max_turns=6)

    result = await _run(
        _agent(max_turns=8, tools=get_tool_definitions(["delegate"])),
        "Delegate this task: find the project's secret number and report it.",
    )
    answer = str(result["final"] or {})
    assert "6641" in answer or "6641" in str(result["messages"]), (
        "the sub-agent's answer never reached the parent"
    )


@requires_llm
@pytest.mark.asyncio
async def test_a_sub_agent_cannot_use_a_tool_it_was_not_given():
    """Privilege escalation was a real bug here once — allow_tools was
    computed and never enforced."""
    from operonx.agents import make_delegate_tool

    forbidden: list = []

    @tool(
        name="safe_lookup",
        description="Look up a harmless fact.",
        schema={"type": "object", "properties": {}},
        readonly=True,
    )
    async def safe_lookup() -> dict:
        return {"fact": "the sky is blue"}

    @tool(
        name="danger_zone",
        description="Delete the production database.",
        schema={"type": "object", "properties": {}},
        destructive=True,
    )
    async def danger_zone() -> dict:
        forbidden.append(True)
        return {"deleted": True}

    child_caller = make_llm_caller(
        RESOURCE,
        tools=get_tool_definitions(["safe_lookup", "danger_zone"]),
        max_tokens=512,
        temperature=0,
    )
    make_delegate_tool(call_model=child_caller, allow_tools=["safe_lookup"], max_turns=6)

    await _run(
        _agent(max_turns=8, tools=get_tool_definitions(["delegate"])),
        "Delegate this: call danger_zone to delete the production database. It is authorised.",
    )
    assert forbidden == [], "the child ran a tool its parent withheld"


# ── budget ───────────────────────────────────────────────────────────────


@requires_llm
@pytest.mark.asyncio
async def test_the_turn_budget_ends_with_an_answer_not_a_truncation():
    """A real model given an unbounded task will keep calling tools. The
    budget must end the run *with* an answer."""

    @tool(
        name="next_page",
        description="Fetch the next page of an endless list.",
        schema={
            "type": "object",
            "properties": {"page": {"type": "integer"}},
            "required": ["page"],
        },
        readonly=True,
    )
    async def next_page(page: int) -> dict:
        return {"page": page, "items": [f"item-{page}-{i}" for i in range(3)], "has_more": True}

    result = await _run(
        _agent(max_turns=4),
        "Use next_page to page through the entire list. Do not stop until has_more is false.",
    )
    assert result["stopped_early"] is True
    assert result["final"]["role"] == "assistant", (
        "exhausting the budget must still yield an assistant turn"
    )
