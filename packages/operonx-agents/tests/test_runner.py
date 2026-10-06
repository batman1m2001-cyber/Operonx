"""The loop, run: turns, the budget, failures, sessions, typed output, trace.

Ported from ``operonx/tests/internal/agents/test_react.py`` and
``test_session.py``: the loop iterates, every tool call is answered, the
last budgeted turn cannot call tools and still answers, a failed model call
leaves the session as it was, a cut answer is reported. Asserted on message
order and turn counts, not just completion: every defect those files
guarded produced a plausible partial answer rather than an error.
"""

from __future__ import annotations

import asyncio

import pytest
from operonx import END, START, Operon, graph, op
from operonx.telemetry.consumers.langfuse import build_tree
from pydantic import BaseModel

from operonx_agents import (
    InMemorySession,
    InMemoryStateStore,
    Runner,
    UsageLimits,
)
from operonx_agents.context.compaction import unmatched_tool_calls
from operonx_agents.context.prompt import prefix_is_stable
from tests.agents import RAN, asks, calls, make, roles, says, strict, tool_ids
from tests.fakes import StatusError, completion


@pytest.fixture(autouse=True)
def _ran():
    RAN.clear()
    yield


class TestTurns:
    async def test_no_tool_call_is_one_turn(self, hub):
        agent, _ = make(hub, says("final"))
        res = await Runner.run(agent, "hi")
        assert (res.status, res.turns, res.output) == ("completed", 1, "final")
        assert roles(res.messages) == ["user", "assistant"]

    async def test_one_tool_turn(self, hub):
        agent, _ = make(hub, asks(("echo", {"a": 1})), says())
        res = await Runner.run(agent, "hi")
        assert res.turns == 2
        assert roles(res.messages) == ["user", "assistant", "tool", "assistant"]
        assert RAN == ["echo:1"]

    async def test_loop_actually_iterates(self, hub):
        agent, _ = make(hub, asks(("echo", {"a": 1})), asks(("echo", {"a": 2}), turn=1), says())
        res = await Runner.run(agent, "hi")
        assert res.turns == 3
        assert roles(res.messages) == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "tool",
            "assistant",
        ]

    async def test_the_opening_messages_survive_and_are_not_duplicated(self, hub):
        agent, llm = make(hub, says())
        opening = [{"role": "user", "content": "hi"}, {"role": "user", "content": "there"}]
        res = await Runner.run(agent, opening)
        assert res.messages[:2] == opening
        assert [m["content"] for m in llm.requests[0]["messages"]] == ["hi", "there"]

    async def test_parallel_tool_calls_all_answered(self, hub):
        specs = [("echo", {"a": i}) for i in range(4)]
        agent, _ = make(hub, asks(*specs), says())
        res = await Runner.run(agent, "hi")
        assert tool_ids(res.messages) == [c["id"] for c in calls(*specs)]

    async def test_the_model_sees_the_tool_results(self, hub):
        agent, llm = make(hub, asks(("echo", {"a": 7})), says())
        await Runner.run(agent, "hi")
        sent = llm.requests[1]["messages"]
        assert sent[-1]["role"] == "tool" and '"a": 7' in sent[-1]["content"]

    async def test_bad_input_is_refused(self, hub):
        agent, _ = make(hub, says())
        with pytest.raises(TypeError, match="Runner input"):
            await Runner.run(agent, 42)


class TestBudget:
    async def test_stops_at_the_cap(self, hub):
        agent, _ = make(hub, asks(("echo", {"a": 1})), limits=UsageLimits(turns=3))
        res = await Runner.run(agent, "hi")
        assert (res.status, res.limit_hit, res.turns) == ("limit", "turns", 3)

    async def test_exhaustion_is_graceful_not_a_cut(self, hub):
        """The model is told, and gets a final turn without tools."""
        agent, llm = make(
            hub,
            asks(("echo", {"a": 1})),
            asks(("echo", {"a": 2}), turn=1),
            says("here is what I have"),
            limits=UsageLimits(turns=3),
        )
        res = await Runner.run(agent, "hi")
        assert roles(res.messages) == [
            "user", "assistant", "tool", "assistant", "tool", "user", "assistant",
        ]  # fmt: skip
        assert "budget" in res.messages[-2]["content"].lower()
        assert res.output == "here is what I have"
        assert [r.get("tool_choice") for r in llm.requests] == [None, None, "none"]
        assert all(r.get("tools") for r in llm.requests), "tool history needs its definitions"

    async def test_finishing_early_is_not_flagged(self, hub):
        agent, _ = make(hub, says(), limits=UsageLimits(turns=3))
        res = await Runner.run(agent, "hi")
        assert (res.status, res.limit_hit) == ("completed", None)

    def test_zero_budget_rejected(self):
        with pytest.raises(ValueError, match="turns=0 must be a positive number"):
            UsageLimits(turns=0)


class TestBudgetNeverStrandsACall:
    """A stubborn model asks for a tool on its last turn though the request
    forbids it. The call is answered "not run", so the history stays one a
    provider accepts."""

    async def run(self, hub, script, turns=2):
        agent, _ = make(hub, *script, limits=UsageLimits(turns=turns))
        return await Runner.run(agent, "hi")

    async def test_the_exhausted_turn_leaves_every_call_answered(self, hub):
        res = await self.run(hub, [asks(("echo", {"a": i}), turn=i) for i in range(3)])
        assert unmatched_tool_calls(res.messages)["calls_without_results"] == []

    async def test_the_unrun_call_is_answered_as_not_run(self, hub):
        res = await self.run(hub, [asks(("echo", {"a": i}), turn=i) for i in range(3)])
        last = res.messages[-1]
        assert last["role"] == "tool" and last["tool_call_id"] == "t1_0"
        assert last["status"] == "error" and "not run" in last["content"].lower()
        assert RAN == ["echo:0"]

    async def test_the_caller_can_tell_there_was_no_answer(self, hub):
        res = await self.run(hub, [asks(("echo", {"a": i}), turn=i) for i in range(3)])
        assert (res.status, res.limit_hit, res.output) == ("limit", "turns", None)

    async def test_a_model_that_answers_on_the_last_turn_is_untouched(self, hub):
        res = await self.run(hub, [asks(("echo", {"a": 0})), says("final")])
        assert roles(res.messages) == ["user", "assistant", "tool", "user", "assistant"]
        assert res.output == "final"


class TestFailuresReachTheModel:
    async def test_unknown_tool_does_not_end_the_run(self, hub):
        agent, _ = make(hub, asks(("nope", {})), says())
        res = await Runner.run(agent, "hi")
        (tool_msg,) = [m for m in res.messages if m["role"] == "tool"]
        assert tool_msg["status"] == "error" and "no tool named" in tool_msg["content"].lower()
        assert (res.status, res.turns) == ("completed", 2)

    async def test_a_destructive_tool_without_an_approver_is_refused(self, hub):
        from operonx_agents import tool

        @tool(destructive=True)
        async def wipe(path: str) -> str:
            """Delete a path."""
            RAN.append("wipe")
            return "gone"

        agent, _ = make(hub, asks(("wipe", {"path": "/"})), says(), tools=[wipe])
        res = await Runner.run(agent, "hi")
        assert RAN == [] and "approval" in res.messages[2]["content"]


class TestFailedTurns:
    """A model call that fails ends the run ``failed``, with no stale answer,
    and the session is left exactly as it was: a retry is possible."""

    async def test_failure_is_not_reported_as_the_previous_answer(self, hub):
        agent, _ = make(hub, says("reply 1"), StatusError(400))
        session = InMemorySession()
        await Runner.run(agent, "one", session=session)
        res = await Runner.run(agent, "two", session=session)
        assert (res.status, res.output) == ("failed", None)
        assert "ModelError" in res.error

    async def test_the_session_is_unchanged_so_a_retry_is_possible(self, hub):
        agent, _ = make(hub, says("reply 1"), StatusError(400), says("reply 2"))
        session = InMemorySession()
        await Runner.run(agent, "one", session=session)
        before = await session.get_items()
        await Runner.run(agent, "two", session=session)
        assert await session.get_items() == before
        res = await Runner.run(agent, "two", session=session)
        assert res.status == "completed"
        assert roles(await session.get_items()) == ["user", "assistant", "user", "assistant"]

    async def test_a_successful_turn_reports_no_error(self, hub):
        agent, _ = make(hub, says("reply 1"))
        res = await Runner.run(agent, "one")
        assert res.error is None and res.output == "reply 1"

    async def test_a_failed_second_turn_keeps_the_first(self, hub):
        """Work already done had effects; the committed turn stays."""
        agent, _ = make(hub, asks(("echo", {"a": 1})), StatusError(400))
        session = InMemorySession()
        res = await Runner.run(agent, "go", session=session)
        assert (res.status, res.turns) == ("failed", 1)
        assert roles(await session.get_items()) == ["user", "assistant", "tool"]


class TestBudgetExhaustionInASession:
    """The exhausted turn is committed with every call answered, so the
    provider accepts the next run on the same session."""

    def make(self, hub):
        script = [asks(("echo", {"a": i}), turn=i) for i in range(3)]
        return make(hub, strict(script), limits=UsageLimits(turns=2))[0]

    async def test_the_exhausted_turn_is_not_reported_as_success(self, hub):
        res = await Runner.run(self.make(hub), "keep going", session=InMemorySession())
        assert (res.status, res.output) == ("limit", None)

    async def test_the_next_run_is_accepted(self, hub):
        agent, session = self.make(hub), InMemorySession()
        await Runner.run(agent, "keep going", session=session)
        res = await Runner.run(agent, "what did you find?", session=session)
        assert res.error is None, res.error

    async def test_work_already_done_is_kept(self, hub):
        agent, session = self.make(hub), InMemorySession()
        await Runner.run(agent, "keep going", session=session)
        ran = [m for m in await session.get_items() if m["role"] == "tool"]
        assert any(m["tool_call_id"] == "t0_0" and m["status"] == "success" for m in ran)


class TestSessions:
    async def test_second_run_sees_the_first(self, hub):
        agent, llm = make(hub, says("reply"))
        session = InMemorySession()
        await Runner.run(agent, "first question", session=session)
        await Runner.run(agent, "second question", session=session)
        sent = [m["content"] for m in llm.requests[1]["messages"]]
        assert sent == ["first question", "reply", "second question"]

    async def test_history_is_not_duplicated(self, hub):
        agent, _ = make(hub, says("reply"))
        session = InMemorySession()
        for q in ("q1", "q2", "q3"):
            await Runner.run(agent, q, session=session)
        items = await session.get_items()
        assert [m["content"] for m in items if m["role"] == "user"] == ["q1", "q2", "q3"]
        assert len([m for m in items if m["role"] == "assistant"]) == 3

    async def test_the_system_prompt_is_first_once_and_never_stored(self, hub):
        agent, llm = make(hub, says(), instructions="be terse")
        session = InMemorySession()
        await Runner.run(agent, "q1", session=session)
        await Runner.run(agent, "q2", session=session)
        for request in llm.requests:
            sent = request["messages"]
            assert sent[0]["role"] == "system" and sent[0]["content"] == "be terse"
            assert sum(m["role"] == "system" for m in sent) == 1
        assert all(m["role"] != "system" for m in await session.get_items())

    async def test_instructions_are_built_from_the_context(self, hub):
        agent, llm = make(hub, says(), instructions=lambda ctx: f"brand: {ctx.deps['brand']}")
        await Runner.run(agent, "q", deps={"brand": "Edupia"})
        assert llm.requests[0]["messages"][0]["content"] == "brand: Edupia"

    async def test_a_session_item_list_returned_is_a_copy(self, hub):
        agent, _ = make(hub, says())
        session = InMemorySession()
        await Runner.run(agent, "q1", session=session)
        (await session.get_items()).append({"role": "user", "content": "injected"})
        assert all(m.get("content") != "injected" for m in await session.get_items())

    async def test_the_prefix_is_byte_stable_across_turns(self, hub):
        agent, llm = make(
            hub, asks(("echo", {"a": 1})), asks(("echo", {"a": 2}), turn=1), says(),
            instructions="be terse",
        )  # fmt: skip
        session = InMemorySession()
        await Runner.run(agent, "q1", session=session)
        await Runner.run(agent, "q2", session=session)
        sent = [r["messages"] for r in llm.requests]
        for previous, current in zip(sent, sent[1:]):
            check = prefix_is_stable(previous, current)
            assert check["stable"], check["diverged"]

    async def test_cache_breakpoints_on_the_system_prompt_and_the_last_message(self, hub):
        agent, llm = make(hub, says(), instructions="be terse")
        await Runner.run(agent, "q1")
        sent = llm.requests[0]["messages"]
        assert [("cache_control" in m) for m in sent] == [True, True]


class TestTruncatedAnswer:
    async def test_a_length_cut_is_reported(self, hub):
        agent, _ = make(hub, completion("The ans", finish_reason="length"))
        res = await Runner.run(agent, "q")
        assert res.truncated and res.finish_reason == "length"

    async def test_a_clean_stop_is_not(self, hub):
        agent, _ = make(hub, says())
        res = await Runner.run(agent, "q")
        assert not res.truncated

    async def test_it_is_the_last_turn_that_counts(self, hub):
        agent, _ = make(hub, asks(("echo", {"a": 1}), finish_reason="length"), says())
        res = await Runner.run(agent, "q")
        assert res.turns == 2 and not res.truncated


class Resolution(BaseModel):
    order_id: str
    refunded: bool


class TestTypedOutput:
    async def test_tool_strategy_answers_through_final_result(self, hub):
        agent, llm = make(
            hub,
            asks(("echo", {"a": 1})),
            asks(("final_result", {"order_id": "A1", "refunded": True}), turn=1),
            output_type=Resolution,
            llm_kw={"structured_output": "tool"},
        )
        res = await Runner.run(agent, "refund A1")
        assert res.status == "completed"
        assert res.output == Resolution(order_id="A1", refunded=True)
        names = [t["function"]["name"] for t in llm.requests[0]["tools"]]
        assert names[-1] == "final_result" and "echo" in names
        assert unmatched_tool_calls(res.messages)["calls_without_results"] == []

    async def test_an_invalid_final_result_is_reasked_with_the_error(self, hub):
        agent, llm = make(
            hub,
            asks(("final_result", {"order_id": "A1"})),
            asks(("final_result", {"order_id": "A1", "refunded": False}), turn=1),
            output_type=Resolution,
            llm_kw={"structured_output": "tool"},
        )
        res = await Runner.run(agent, "refund A1")
        assert res.output == Resolution(order_id="A1", refunded=False)
        error = llm.requests[1]["messages"][-1]
        assert error["role"] == "tool" and "refunded" in error["content"]

    async def test_still_invalid_after_the_retries_fails(self, hub):
        agent, _ = make(
            hub,
            asks(("final_result", {"order_id": "A1"})),
            output_type=Resolution,
            output_retries=1,
            llm_kw={"structured_output": "tool"},
        )
        res = await Runner.run(agent, "refund A1")
        assert res.status == "failed" and "OutputInvalid" in res.error
        assert res.turns == 2

    async def test_native_strategy_validates_the_text(self, hub):
        agent, llm = make(
            hub,
            says('{"order_id": "A1"}'),
            says('{"order_id": "A1", "refunded": true}'),
            output_type=Resolution,
            tools=[],
            llm_kw={"structured_output": "native"},
        )
        res = await Runner.run(agent, "refund A1")
        assert res.output.refunded is True
        assert llm.requests[0]["response_format"]["type"] == "json_schema"
        assert "refunded" in llm.requests[1]["messages"][-1]["content"]

    async def test_prompted_strategy_puts_the_schema_in_the_system_prompt(self, hub):
        agent, llm = make(
            hub,
            says('Sure: {"order_id": "A1", "refunded": false}'),
            output_type=Resolution,
            tools=[],
            instructions="You refund.",
            llm_kw={"structured_output": "prompted"},
        )
        res = await Runner.run(agent, "refund A1")
        assert res.output.order_id == "A1"
        system = llm.requests[0]["messages"][0]["content"]
        assert system.startswith("You refund.") and '"refunded"' in system


@op(exclude={"trace": ["agent"]})
async def support(agent, question: str) -> dict:
    res = await Runner.run(agent, question)
    return {"answer": res.output}


@graph
def chat(agent, question):
    s = support(agent=agent, question=question)
    START >> s >> END


@pytest.fixture
def traced_agent(hub):
    agent, _ = make(
        hub, asks(("echo", {"a": 1}), ("note", {"text": "x"})), says("done"),
    )  # fmt: skip
    return agent


async def test_the_trace_reads_as_the_conversation(traced_agent):
    """agent op → turn[n] → model, tool: one root, a row per step."""
    engine = Operon(chat, params={"agent": None, "question": None})
    handle = engine.start({"agent": traced_agent, "question": "hi"})
    assert (await handle.result())["answer"] == "done"
    tree = build_tree(handle.trace)
    records = {n.op_id: n for n in handle.trace.nodes}
    rows = [(records[i].op_name, records[t["parent"]].op_name if t["parent"] else None)
            for i, t in tree.items() if t["kind"] == "record"]  # fmt: skip
    assert sorted(rows, key=str) == sorted(
        [
            ("s", None),
            ("turn", "s"),
            ("turn", "s"),
            ("model", "turn"),
            ("model", "turn"),
            ("echo", "turn"),
            ("note", "turn"),
        ],
        key=str,
    )
    model = next(n for n in handle.trace.nodes if n.op_name == "model")
    assert (
        model.attrs["gen_ai.operation.name"] == "chat" and model.outputs["usage"]["requests"] == 1
    )
    turn = next(n for n in handle.trace.nodes if n.op_name == "turn")
    assert turn.attrs["gen_ai.agent.name"] == "agent"


async def test_resuming_a_finished_run_returns_its_result(hub):
    agent, llm = make(hub, says("done"))
    store = InMemoryStateStore()
    res = await Runner.run(agent, "hi", store=store)
    again = await Runner.resume(agent, res.run_id, store=store)
    assert again.to_dict() == res.to_dict() and llm.calls == 1


async def test_resuming_a_failed_run_retries_its_turn(hub):
    agent, _ = make(hub, StatusError(400), says("done"))
    store, session = InMemoryStateStore(), InMemorySession()
    res = await Runner.run(agent, "hi", store=store, session=session)
    assert res.status == "failed" and await session.get_items() == []
    again = await Runner.resume(agent, res.run_id, store=store, session=session)
    assert (again.status, again.output) == ("completed", "done")
    assert roles(await session.get_items()) == ["user", "assistant"]


async def test_resume_checks_the_agent(hub):
    agent, _ = make(hub, says())
    store = InMemoryStateStore()
    res = await Runner.run(agent, "hi", store=store)
    with pytest.raises(ValueError, match="belongs to agent"):
        await Runner.resume(agent.clone(name="other"), res.run_id, store=store)
    with pytest.raises(KeyError):
        await Runner.resume(agent, "missing", store=store)


async def test_durability_exit_writes_once_at_the_end(hub):
    agent, _ = make(hub, asks(("echo", {"a": 1})), says())

    class Counting(InMemorySession):
        writes = 0

        async def add_items(self, items):
            Counting.writes += 1
            await super().add_items(items)

    session = Counting()
    res = await Runner.run(agent, "hi", session=session, durability="exit")
    assert Counting.writes == 1 and roles(await session.get_items()) == roles(res.messages)


async def test_a_streamed_run_left_early_is_cancelled(hub):
    agent, llm = make(hub, asks(("slow", {"seconds": 5})), says())
    session = InMemorySession()
    stream = Runner.stream(agent, "hi", session=session)
    async for event in stream:
        if type(event).__name__ == "ToolCallStarted":
            break
    await stream.aclose()
    await asyncio.sleep(0)
    assert await session.get_items() == [] and RAN == []
