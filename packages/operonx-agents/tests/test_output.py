"""Output strategies: each validates the same pydantic model and re-asks an
invalid answer at most ``output_retries`` times; ``tool`` mode catches the
out-of-enum argument ``qwen3.7-plus`` was measured sending."""

from __future__ import annotations

import json
import math
from typing import List, Literal

import pytest
from pydantic import BaseModel

from operonx_agents import Choice, Model, OutputInvalid, ask
from operonx_agents.model.output import FINAL_TOOL, shape_for
from tests.fakes import ScriptedLLM, completion

MSGS = [{"role": "system", "content": "Classify."}, {"role": "user", "content": "I'm busy"}]


class Resolution(BaseModel):
    intent: Literal["agree", "busy", "refuse"]
    callback_hour: int
    notes: List[str] = []


GOOD = {"intent": "busy", "callback_hour": 17}
BAD = {"intent": "banana", "callback_hour": "later"}


def answer(strategy: str, obj) -> object:
    """``obj`` the way each strategy's gateway returns it."""
    if strategy == "tool":
        return completion(
            tool_calls=[{"id": "t1", "name": FINAL_TOOL, "args": obj}], finish_reason="tool_calls"
        )
    if strategy == "prompted":
        return completion(f"Sure! Here it is:\n```json\n{json.dumps(obj)}\n```")
    return completion(json.dumps(obj))


@pytest.mark.parametrize("strategy", ["native", "tool", "prompted"])
class TestEachStrategy:
    async def test_validates_into_the_model(self, hub, strategy):
        hub(m=ScriptedLLM(answer(strategy, GOOD), structured_output=strategy))
        result = await ask(Model("m"), MSGS, shape_for(Resolution))
        assert result.value == Resolution(intent="busy", callback_hour=17)
        assert (result.strategy, result.asks) == (strategy, 1)

    async def test_reasks_with_the_error_then_succeeds(self, hub, strategy):
        llm = ScriptedLLM(answer(strategy, BAD), answer(strategy, GOOD), structured_output=strategy)
        hub(m=llm)
        result = await ask(Model("m"), MSGS, shape_for(Resolution), output_retries=1)
        assert result.value.intent == "busy" and result.asks == 2
        reask = llm.requests[1]["messages"]
        assert len(reask) == len(MSGS) + 2, "the bad answer and the correction are both sent"
        fix = reask[-1]["content"]
        assert "intent: Input should be 'agree', 'busy' or 'refuse'" in fix
        assert "callback_hour: Input should be a valid integer" in fix
        if strategy == "tool":
            assert reask[-2]["tool_calls"][0]["name"] == FINAL_TOOL
            assert reask[-1]["role"] == "tool" and reask[-1]["tool_call_id"] == "t1"

    @pytest.mark.parametrize("retries", [0, 1, 2])
    async def test_reasks_at_most_output_retries_times(self, hub, strategy, retries):
        llm = ScriptedLLM(answer(strategy, BAD), structured_output=strategy)
        hub(m=llm)
        with pytest.raises(OutputInvalid, match=f"after {retries} re-ask") as exc:
            await ask(Model("m"), MSGS, shape_for(Resolution), output_retries=retries)
        assert llm.calls == retries + 1
        assert "intent" in exc.value.error

    async def test_usage_adds_up_over_reasks(self, hub, strategy):
        hub(
            m=ScriptedLLM(answer(strategy, BAD), answer(strategy, GOOD), structured_output=strategy)
        )
        result = await ask(Model("m"), MSGS, shape_for(Resolution))
        assert result.usage.requests == 2 and result.usage.input_tokens == 20


class TestRequestShape:
    async def test_native_sends_json_schema(self, hub):
        llm = ScriptedLLM(completion('{"intent": "busy"}'), structured_output="native")
        hub(m=llm)
        await ask(Model("m"), MSGS, shape_for(Choice(["agree", "busy"], field="intent")))
        fmt = llm.requests[0]["response_format"]
        assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
        assert fmt["json_schema"]["schema"]["properties"]["intent"]["enum"] == ["agree", "busy"]
        assert llm.requests[0]["messages"] == MSGS, "native leaves the prompt alone"

    async def test_strict_only_when_every_property_is_required(self, hub):
        llm = ScriptedLLM(completion(json.dumps(GOOD)), structured_output="native")
        hub(m=llm)
        await ask(Model("m"), MSGS, shape_for(Resolution))
        assert llm.requests[0]["response_format"]["json_schema"]["strict"] is False

    async def test_tool_forces_the_final_result_tool(self, hub):
        llm = ScriptedLLM(answer("tool", GOOD), structured_output="tool")
        hub(m=llm)
        await ask(Model("m"), MSGS, shape_for(Resolution))
        sent = llm.requests[0]
        assert sent["tool_choice"] == {"type": "function", "function": {"name": FINAL_TOOL}}
        assert sent["tools"][0]["function"]["name"] == FINAL_TOOL

    async def test_prompted_describes_the_schema_on_the_last_user_turn(self, hub):
        llm = ScriptedLLM(answer("prompted", GOOD), structured_output="prompted")
        hub(m=llm)
        await ask(Model("m"), MSGS, shape_for(Resolution))
        sent = llm.requests[0]["messages"]
        assert sent[0] == MSGS[0]
        assert sent[1]["content"].startswith("I'm busy\n\nAnswer with one JSON object")
        assert MSGS[1]["content"] == "I'm busy", "the caller's message is not mutated"

    async def test_text_output_is_the_content(self, hub):
        hub(m=ScriptedLLM(completion("Hello there")))
        result = await ask(Model("m"), MSGS, shape_for(str))
        assert result.value == "Hello there"

    async def test_resources_declaring_different_strategies_are_refused(self, hub):
        hub(
            a=ScriptedLLM(completion("{}"), structured_output="native"),
            b=ScriptedLLM(completion("{}"), structured_output="tool"),
        )
        with pytest.raises(ValueError, match="declare different structured_output"):
            await ask(Model("a", fallback=["b"]), MSGS, shape_for(Resolution))


class TestQwenToolCase:
    async def test_out_of_enum_tool_argument_is_caught(self, hub):
        """qwen3.7-plus forced the tool but sent {"intent": "banana"} (§2c)."""
        llm = ScriptedLLM(answer("tool", {"intent": "banana"}), structured_output="tool")
        hub(m=llm)
        shape = shape_for(Choice(["agree", "refuse", "busy", "unclear"], field="intent"))
        with pytest.raises(OutputInvalid, match="intent: Input should be"):
            await ask(Model("m"), MSGS, shape, output_retries=1)
        assert llm.calls == 2

    async def test_a_reply_with_no_tool_call(self, hub):
        hub(m=ScriptedLLM(completion("busy"), structured_output="tool"))
        with pytest.raises(OutputInvalid, match="no call to final_result"):
            await ask(Model("m"), MSGS, shape_for(Resolution), output_retries=0)


class TestChoice:
    def test_from_input_reads_the_labels_per_call(self):
        c = Choice(from_input="allowed", field="intent")
        assert shape_for(c, {"allowed": ["a", "b"]}).labels == ("a", "b")
        assert shape_for(c, {"allowed": ["x"]}).labels == ("x",)

    def test_missing_input_names_it(self):
        with pytest.raises(KeyError, match="no input 'allowed'"):
            shape_for(Choice(from_input="allowed"), {})

    def test_empty_set_is_refused(self):
        with pytest.raises(ValueError, match="empty"):
            shape_for(Choice(from_input="allowed"), {"allowed": []})

    def test_exactly_one_source(self):
        with pytest.raises(ValueError, match="exactly one"):
            Choice(["a"], from_input="b")


class TestConfidence:
    async def test_probability_of_the_label_tokens(self, hub):
        tokens = [
            ('{"', -0.001),
            ("intent", -0.001),
            ('":', 0.0),
            (' "', 0.0),
            ("bu", -0.2),
            ("sy", -0.1),
            ('"}', 0.0),
        ]
        hub(
            m=ScriptedLLM(
                completion('{"intent": "busy"}', logprobs=tokens), structured_output="native"
            )
        )
        result = await ask(Model("m"), MSGS, shape_for(Choice(["agree", "busy"], field="intent")))
        assert result.value == "busy"
        assert result.confidence == pytest.approx(math.exp(-0.3))

    async def test_a_trailing_stop_token_is_ignored(self, hub):
        """vLLM's list ends with the stop token (gemma's "<eos>")."""
        tokens = [('{"intent": "', 0.0), ("busy", -0.1), ('"}', 0.0), ("<eos>", -0.01)]
        hub(
            m=ScriptedLLM(
                completion('{"intent": "busy"}', logprobs=tokens), structured_output="native"
            )
        )
        result = await ask(Model("m"), MSGS, shape_for(Choice(["busy"], field="intent")))
        assert result.confidence == pytest.approx(math.exp(-0.1))

    async def test_none_without_logprobs(self, hub):
        hub(m=ScriptedLLM(completion('{"intent": "busy"}'), structured_output="native"))
        result = await ask(Model("m"), MSGS, shape_for(Choice(["busy"], field="intent")))
        assert result.confidence is None

    async def test_none_when_tokens_do_not_spell_the_answer(self, hub):
        hub(
            m=ScriptedLLM(
                completion('{"intent": "busy"}', logprobs=[("x", -1.0)]), structured_output="native"
            )
        )
        result = await ask(Model("m"), MSGS, shape_for(Choice(["busy"], field="intent")))
        assert result.confidence is None
