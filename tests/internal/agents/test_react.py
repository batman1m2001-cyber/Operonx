"""The ReAct loop, run rather than built.

A graph that compiles is not a graph that runs. Every defect in §15.1
V6–V10 was invisible at build time: the loop capped at one iteration, an
op after it never became ready, `collect()` handed over a dict where the
reducer wanted a list. All of them produced a *plausible* partial answer
rather than an error, so these tests assert on message ordering and turn
counts, not just on completion.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.agents.graphs.react import (
    EMPTY_RESULT,
    agent_result,
    build_react_agent,
)
from operonx.agents.tool import clear_registry, tool
from operonx.checkpoint import bind_interrupt_bus
from operonx.core import Operon, op

pytestmark = pytest.mark.unit

NUM = {"type": "object", "properties": {"a": {"type": "number"}}, "required": ["a"]}
CALL = [{"id": "t0", "name": "echo", "args": {"a": 1}}]
USER = [{"id": "u0", "role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def _tools():
    clear_registry()
    deleted = []

    @tool(name="echo", description="Echo a number.", schema=NUM)
    async def echo(a: float) -> dict:
        return {"a": a}

    @tool(name="wipe", description="Delete everything.", schema=NUM, destructive=True)
    async def wipe(a: float) -> dict:
        deleted.append(a)
        return {"gone": a}

    yield deleted
    clear_registry()


def scripted_model(script):
    """A model op replaying ``script`` — [(tool_calls, done), ...].

    Past the end it answers and finishes, so a runaway loop shows up as a
    turn count rather than a hang.
    """
    state = {"i": 0}

    @op
    def call_model(messages: list = None) -> dict:
        i = state["i"]
        state["i"] += 1
        calls, done = script[i] if i < len(script) else ([], True)
        return {
            "assistant_message": [
                {"id": f"a{i}", "role": "assistant", "content": "final" if done else f"turn {i}"}
            ],
            "tool_calls": calls,
            "done": done,
        }

    return call_model


async def run_agent(script, *, max_turns=25, answer=None, approval_timeout=5.0, messages=USER):
    built = build_react_agent(
        call_model=scripted_model(script),
        max_turns=max_turns,
        approval_timeout=approval_timeout,
    )(messages=None)
    engine = Operon(built)
    handle = engine.start(inputs={"messages": messages})
    prompts = []

    def sink(evt):
        prompts.append(evt.payload)
        if answer is not None:
            handle.state.resume_interrupt(evt.interrupt_id, answer)

    bind_interrupt_bus(handle.state, sink=sink)
    await asyncio.wait_for(handle.result(), timeout=60)
    # handle.result() is built from emitted frames and carries no state,
    # so read the cells through the handle's own MemoryState.
    return agent_result(handle.state, built), prompts


def roles(result):
    return [m.get("role") for m in result["messages"]]


class TestTurns:
    @pytest.mark.asyncio
    async def test_no_tool_call_is_one_turn(self):
        result, _ = await run_agent([([], True)])
        assert result["turns"] == 1
        assert roles(result) == ["user", "assistant"]
        assert result["final"]["content"] == "final"

    @pytest.mark.asyncio
    async def test_one_tool_turn(self):
        result, _ = await run_agent([(CALL, False)])
        assert result["turns"] == 2
        assert roles(result) == ["user", "assistant", "tool", "assistant"]

    @pytest.mark.asyncio
    async def test_loop_actually_iterates(self):
        """The defect this guards capped every such loop at one turn, and
        did it silently — the answer just looked short."""
        result, _ = await run_agent([(CALL, False), (CALL, False)])
        assert result["turns"] == 3
        assert roles(result) == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "tool",
            "assistant",
        ]

    @pytest.mark.asyncio
    async def test_the_opening_messages_survive(self):
        result, _ = await run_agent([([], True)])
        assert result["messages"][0]["content"] == "hi"

    @pytest.mark.asyncio
    async def test_opening_messages_without_ids_are_not_duplicated(self):
        """`add_messages` upserts on id, so an id-less opening turn is the
        only way a double-seed shows up. An explicit seeding op wrote the
        input a second time; every test here used ids, so it stayed
        invisible until an example printed the conversation."""
        plain = [{"role": "user", "content": "hi"}]
        result, _ = await run_agent([([], True)], messages=plain)
        assert [m["role"] for m in result["messages"]] == ["user", "assistant"]

    @pytest.mark.asyncio
    async def test_parallel_tool_calls_all_answered(self):
        """Every tool_call needs a matching result or the provider 400s."""
        calls = [{"id": f"t{i}", "name": "echo", "args": {"a": i}} for i in range(4)]
        result, _ = await run_agent([(calls, False)])
        tool_ids = {m["tool_call_id"] for m in result["messages"] if m.get("role") == "tool"}
        assert tool_ids == {"t0", "t1", "t2", "t3"}


class TestBudget:
    @pytest.mark.asyncio
    async def test_stops_at_the_cap(self):
        never_finishes = [(CALL, False)] * 50
        result, _ = await run_agent(never_finishes, max_turns=3)
        assert result["turns"] == 3
        assert result["stopped_early"] is True

    @pytest.mark.asyncio
    async def test_exhaustion_is_graceful_not_a_cut(self):
        """The model is told, and gets a final turn — otherwise the caller
        receives a truncated run with no answer in it."""
        result, _ = await run_agent([(CALL, False)] * 50, max_turns=3)
        assert roles(result) == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "tool",
            "user",
            "assistant",
        ]
        notice = result["messages"][-2]
        assert "budget" in notice["content"].lower()
        assert result["final"]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_finishing_early_is_not_flagged_as_stopped(self):
        result, _ = await run_agent([([], True)], max_turns=3)
        assert result["stopped_early"] is False

    def test_zero_budget_rejected(self):
        with pytest.raises(ValueError, match=r"max_turns must be >= 1"):
            build_react_agent(call_model=scripted_model([]), max_turns=0)


class TestFailuresReachTheModel:
    @pytest.mark.asyncio
    async def test_unknown_tool_does_not_end_the_run(self):
        bad = [{"id": "t0", "name": "nope", "args": {}}]
        result, _ = await run_agent([(bad, False)])
        tool_msgs = [m for m in result["messages"] if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["status"] == "error"
        assert result["turns"] == 2, "the model gets another turn to correct itself"


class TestDestructiveTools:
    @pytest.mark.asyncio
    async def test_approval_gates_the_call(self, _tools):
        calls = [{"id": "t0", "name": "wipe", "args": {"a": 5}}]
        result, prompts = await run_agent([(calls, False)], answer={"approved": True})
        assert prompts[0]["tool"] == "wipe"
        assert _tools == [5]
        assert result["turns"] == 2

    @pytest.mark.asyncio
    async def test_denial_still_produces_a_tool_message(self, _tools):
        calls = [{"id": "t0", "name": "wipe", "args": {"a": 5}}]
        result, _ = await run_agent([(calls, False)], answer={"approved": False})
        assert _tools == []
        tool_msgs = [m for m in result["messages"] if m.get("role") == "tool"]
        assert len(tool_msgs) == 1 and tool_msgs[0]["status"] == "error"


class TestAgentResult:
    def test_missing_state_gives_the_empty_shape(self):
        """Callers index ["messages"] unconditionally; an empty answer
        means an op raised, not that the agent was silent."""
        assert agent_result({}, object()) == EMPTY_RESULT

    @pytest.mark.asyncio
    async def test_final_is_the_last_assistant_message(self):
        result, _ = await run_agent([(CALL, False)])
        assert result["final"] is result["messages"][-1]
        assert result["final"]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_messages_are_flat_not_per_turn_snapshots(self):
        """Indexing the raw run() dict yields a list of per-iteration
        writes; agent_result must return the merged conversation."""
        result, _ = await run_agent([(CALL, False), (CALL, False)])
        assert all(isinstance(m, dict) for m in result["messages"])


def calling_model(script):
    """Like :func:`scripted_model`, but shaped the way ``adapt_llm_output``
    shapes a real provider turn: the assistant message **carries** its
    ``tool_calls``. ``scripted_model`` leaves them off, which is why a
    history ending on an unanswered call looked valid to every test above.
    """
    state = {"i": 0}

    @op
    def call_model(messages: list = None) -> dict:
        i = state["i"]
        state["i"] += 1
        calls, done = script[i] if i < len(script) else ([], True)
        message = {"id": f"a{i}", "role": "assistant", "content": "" if calls else "final"}
        if calls:
            message["tool_calls"] = calls
        return {"assistant_message": [message], "tool_calls": calls, "done": done}

    return call_model


async def run_calling(script, *, max_turns=25):
    built = build_react_agent(call_model=calling_model(script), max_turns=max_turns)(messages=None)
    handle = Operon(built).start(inputs={"messages": USER})
    await asyncio.wait_for(handle.result(), timeout=60)
    return agent_result(handle.state, built)


def _call(i):
    return [{"id": f"t{i}", "name": "echo", "args": {"a": i}}]


class TestBudgetNeverStrandsACall:
    """A model that ignores the budget notice asks for a tool on its last
    turn. ``decide`` ends the loop without dispatching — but the assistant
    message, ``tool_calls`` and all, was already written to the history.
    Providers reject a conversation with an unanswered ``tool_call``, so
    the *next* request failed, one exchange away from the cause.
    """

    @pytest.mark.asyncio
    async def test_the_exhausted_turn_leaves_every_call_answered(self):
        from operonx.agents.ops.compact_ops import unmatched_tool_calls

        result = await run_calling([(_call(i), False) for i in range(10)], max_turns=2)
        assert unmatched_tool_calls(result["messages"])["calls_without_results"] == []

    @pytest.mark.asyncio
    async def test_the_unrun_call_is_answered_as_not_run(self):
        result = await run_calling([(_call(i), False) for i in range(10)], max_turns=2)
        last = result["messages"][-1]
        assert last["role"] == "tool" and last["tool_call_id"] == "t1"
        assert last["status"] == "error"
        assert "budget" in last["content"].lower()

    @pytest.mark.asyncio
    async def test_the_caller_can_tell_there_was_no_answer(self):
        result = await run_calling([(_call(i), False) for i in range(10)], max_turns=2)
        assert result["stopped_early"] is True
        assert result["final"] is None, "a tool request is not an answer"

    @pytest.mark.asyncio
    async def test_a_model_that_answers_on_the_last_turn_is_untouched(self):
        result = await run_calling([(_call(0), False), ([], True)], max_turns=2)
        assert [m["role"] for m in result["messages"]] == [
            "user",
            "assistant",
            "tool",
            "user",
            "assistant",
        ]
        assert result["final"]["content"] == "final"

    @pytest.mark.asyncio
    async def test_calls_on_a_turn_marked_done_are_answered_too(self):
        """``decide`` also ends the loop on ``done`` alone. A call_model
        that says done *and* asks for a tool strands it the same way."""
        from operonx.agents.ops.compact_ops import unmatched_tool_calls

        result = await run_calling([(_call(0), True)], max_turns=5)
        assert unmatched_tool_calls(result["messages"])["calls_without_results"] == []


def ending_model(finish_reason, **extra):
    """Answers at once, reporting ``finish_reason`` the way the
    ``make_llm_caller`` adapter does."""

    @op
    def call_model(messages: list = None) -> dict:
        return {
            "assistant_message": [{"id": "a0", "role": "assistant", "content": "The answer is"}],
            "tool_calls": [],
            "done": True,
            "finish_reason": finish_reason,
            **extra,
        }

    return call_model


async def run_model(call_model, *, max_turns=5):
    built = build_react_agent(call_model=call_model, max_turns=max_turns)(messages=None)
    handle = Operon(built).start(inputs={"messages": USER})
    await asyncio.wait_for(handle.result(), timeout=60)
    return agent_result(handle.state, built)


class TestATruncatedAnswerIsReported:
    """``finish_reason`` and ``truncated`` were declared on the adapter and
    read by nothing: an answer cut at ``finish_reason="length"`` ended the
    loop as a clean finish, and the caller had no way to tell — though
    the module docstring promised one."""

    @pytest.mark.asyncio
    async def test_a_length_cut_is_flagged(self):
        result = await run_model(ending_model("length", truncated=True))
        assert result["truncated"] is True
        assert result["finish_reason"] == "length"
        assert result["stopped_early"] is True, "a cut answer is not a finished one"

    @pytest.mark.asyncio
    async def test_the_reason_alone_is_enough(self):
        """A hand-written call_model may report the reason and not the flag."""
        result = await run_model(ending_model("max_tokens"))
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_a_clean_stop_is_not_flagged(self):
        result = await run_model(ending_model("stop"))
        assert result["truncated"] is False
        assert result["finish_reason"] == "stop"
        assert result["stopped_early"] is False

    @pytest.mark.asyncio
    async def test_a_call_model_that_reports_nothing_reads_as_clean(self):
        result = await run_calling([([], True)])
        assert result["truncated"] is False
        assert result["finish_reason"] == ""

    @pytest.mark.asyncio
    async def test_it_is_the_last_turn_that_counts(self):
        """A cut mid-run that the model recovered from is not the answer."""
        state = {"i": 0}

        @op
        def call_model(messages: list = None) -> dict:
            i = state["i"]
            state["i"] += 1
            calls = _call(0) if i == 0 else []
            message = {"id": f"a{i}", "role": "assistant", "content": "" if calls else "done"}
            if calls:
                message["tool_calls"] = calls
            return {
                "assistant_message": [message],
                "tool_calls": calls,
                "done": not calls,
                "finish_reason": "length" if i == 0 else "stop",
            }

        result = await run_model(call_model)
        assert result["turns"] == 2
        assert result["truncated"] is False

    @pytest.mark.asyncio
    async def test_the_llm_adapter_is_wired_through(self):
        """End to end through ``make_llm_caller`` and a provider that stops
        at ``length`` — the path a real deployment takes."""
        from unittest.mock import Mock, patch

        from openai.types.chat.chat_completion import ChatCompletion

        from operonx.agents.ops.model_ops import make_llm_caller

        async def generate(messages, **kwargs):
            return ChatCompletion.model_validate(
                {
                    "id": "x",
                    "created": 0,
                    "model": "m",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "length",
                            "message": {"role": "assistant", "content": "The answer is"},
                        }
                    ],
                }
            )

        llm = Mock()
        llm.generate = generate
        hub = Mock()
        hub.get.return_value = llm
        with patch("operonx.providers.ops._utils.ResourceHub") as hub_cls:
            hub_cls.instance.return_value = hub
            result = await run_model(make_llm_caller("mock"))

        assert result["final"]["content"] == "The answer is"
        assert result["truncated"] is True
        assert result["finish_reason"] == "length"


def _stubborn_llm(seen):
    """A provider whose model ignores the budget notice: it asks for a tool
    on every turn, unless the request itself forbids tools."""
    from unittest.mock import Mock

    from openai.types.chat.chat_completion import ChatCompletion

    async def generate(messages, **kwargs):
        seen.append(kwargs)
        if kwargs.get("tools") and kwargs.get("tool_choice") != "none":
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{len(seen)}",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"a": 1}'},
                    }
                ],
            }
            reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": "Here is what I have."}
            reason = "stop"
        return ChatCompletion.model_validate(
            {
                "id": "x",
                "created": 0,
                "model": "m",
                "object": "chat.completion",
                "choices": [{"index": 0, "finish_reason": reason, "message": message}],
            }
        )

    llm = Mock()
    llm.generate = generate
    return llm


class TestTheLastTurnCannotCallTools:
    """The budget notice asks the model to answer; a model can ignore it.
    It used to be sent the tools on the final turn all the same, so a
    stubborn model asked for another one, the call was answered "not
    run", and the caller got no answer at all. On the last turn the model
    is now called with ``tool_choice="none"`` — the API, not the prose,
    says it must answer in text."""

    @pytest.mark.asyncio
    async def test_a_stubborn_model_still_answers(self):
        from unittest.mock import Mock, patch

        from operonx.agents.ops.model_ops import make_llm_caller
        from operonx.agents.tool import get_tool_definitions

        seen = []
        hub = Mock()
        hub.get.return_value = _stubborn_llm(seen)
        with patch("operonx.providers.ops._utils.ResourceHub") as hub_cls:
            hub_cls.instance.return_value = hub
            caller = make_llm_caller("mock", tools=get_tool_definitions(["echo"]))
            result = await run_model(caller, max_turns=3)

        assert result["final"] is not None, "the budget ran out with no answer"
        assert result["final"]["content"] == "Here is what I have."
        assert result["turns"] == 3
        assert result["stopped_early"] is True, "the budget did run out"
        assert [kw.get("tool_choice") for kw in seen] == [None, None, "none"]
        assert all(kw.get("tools") for kw in seen), "tool history needs its definitions"

    @pytest.mark.asyncio
    async def test_a_caller_given_tool_choice_keeps_it_until_the_last_turn(self):
        from unittest.mock import Mock, patch

        from operonx.agents.ops.model_ops import make_llm_caller
        from operonx.agents.tool import get_tool_definitions

        seen = []
        hub = Mock()
        hub.get.return_value = _stubborn_llm(seen)
        with patch("operonx.providers.ops._utils.ResourceHub") as hub_cls:
            hub_cls.instance.return_value = hub
            caller = make_llm_caller(
                "mock", tools=get_tool_definitions(["echo"]), tool_choice="required"
            )
            await run_model(caller, max_turns=2)

        assert [kw.get("tool_choice") for kw in seen] == ["required", "none"]

    @pytest.mark.asyncio
    async def test_a_hand_written_call_model_is_told_which_turn_is_last(self):
        flags = []

        @op
        def call_model(messages: list = None, last_turn: bool = False) -> dict:
            flags.append(last_turn)
            calls = [] if last_turn else _call(len(flags))
            message = {
                "id": f"a{len(flags)}",
                "role": "assistant",
                "content": "" if calls else "ok",
            }
            if calls:
                message["tool_calls"] = calls
            return {"assistant_message": [message], "tool_calls": calls, "done": not calls}

        result = await run_model(call_model, max_turns=3)
        assert flags == [False, False, True]
        assert result["final"]["content"] == "ok"
