"""``llm_step`` in a graph: an out-of-set label degrades to ``on_invalid``,
a slow model to ``on_timeout`` within 20 ms of the deadline (A0 measured
4.4 ms), and without a degrade value the failure is the op's."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from operonx import END, START, Operon, graph
from pydantic import BaseModel

from operonx_agents import Choice, Model, ModelSettings, llm_step
from tests.fakes import ScriptedLLM, StatusError, completion

ALLOWED = ["agree", "busy", "wrong_number"]


def classifier(**kw):
    defaults = dict(
        model=Model("inhouse", deadline=0.3),
        system="{analyzer_system_prompt}",
        user="{intent_prompt}",
        output=Choice(from_input="allowed_intents", field="intent"),
    )
    return llm_step(**{**defaults, **kw})


def engine(step):
    @graph
    def turn(analyzer_system_prompt, intent_prompt, allowed_intents):
        classify = step(
            analyzer_system_prompt=analyzer_system_prompt,
            intent_prompt=intent_prompt,
            allowed_intents=allowed_intents,
        )
        START >> classify >> END

    return Operon(
        turn,
        params={"analyzer_system_prompt": None, "intent_prompt": None, "allowed_intents": None},
    )


INPUTS = {
    "analyzer_system_prompt": "You classify.",
    "intent_prompt": "khách: anh bận",
    "allowed_intents": ALLOWED,
}


async def test_ok(hub):
    llm = ScriptedLLM(
        completion(
            '{"intent": "busy"}', logprobs=[('{"intent": "', 0.0), ("busy", -0.05), ('"}', 0.0)]
        ),
        structured_output="native",
    )
    hub(inhouse=llm)
    out = await engine(classifier(settings=ModelSettings(logprobs=True))).run(INPUTS)
    assert (out["value"], out["outcome"], out["error"]) == ("busy", "ok", None)
    assert out["confidence"] == pytest.approx(0.951, abs=1e-3)
    assert out["model_used"] == "inhouse" and out["usage"]["requests"] == 1
    sent = llm.requests[0]
    assert sent["messages"] == [
        {"role": "system", "content": "You classify."},
        {"role": "user", "content": "khách: anh bận"},
    ]
    assert (
        sent["response_format"]["json_schema"]["schema"]["properties"]["intent"]["enum"] == ALLOWED
    )
    assert sent["logprobs"] is True


async def test_the_allow_list_is_read_per_call(hub):
    llm = ScriptedLLM(completion('{"intent": "agree"}'), structured_output="native")
    hub(inhouse=llm)
    eng = engine(classifier())
    await eng.run({**INPUTS, "allowed_intents": ["agree"]})
    await eng.run({**INPUTS, "allowed_intents": ["agree", "busy"]})
    enums = [
        r["response_format"]["json_schema"]["schema"]["properties"]["intent"]["enum"]
        for r in llm.requests
    ]
    assert enums == [["agree"], ["agree", "busy"]]


async def test_out_of_set_label_goes_to_on_invalid(hub):
    llm = ScriptedLLM(completion('{"intent": "banana"}'), structured_output="native")
    hub(inhouse=llm)
    out = await engine(classifier(on_invalid="fallback", output_retries=1)).run(INPUTS)
    assert (out["value"], out["outcome"], out["confidence"]) == ("fallback", "invalid", 0.0)
    assert "banana" not in str(out["value"]) and "intent: Input should be" in out["error"]
    assert llm.calls == 2


async def test_slow_model_goes_to_on_timeout_within_20ms(hub):
    hub(inhouse=ScriptedLLM(completion('{"intent": "busy"}'), delay=5, structured_output="native"))
    eng = engine(classifier(model=Model("inhouse", deadline=0.1), on_timeout="fallback"))
    late = []
    for _ in range(5):
        start = time.perf_counter()
        out = await eng.run(INPUTS)
        late.append((time.perf_counter() - start) * 1000 - 100)
        assert (out["value"], out["outcome"]) == ("fallback", "timeout")
        assert "0.1s deadline" in out["error"]
    assert 0 <= min(late) and max(late) < 20, late


async def test_a_dict_degrade_sets_outputs_by_name(hub):
    hub(inhouse=ScriptedLLM(StatusError(500), structured_output="native"))
    out = await engine(classifier(on_error={"value": "fallback", "confidence": 0.25})).run(INPUTS)
    assert (out["value"], out["confidence"], out["outcome"]) == ("fallback", 0.25, "error")


def test_a_degrade_naming_an_unknown_output_is_refused():
    with pytest.raises(ValueError, match=r"\['intent'\]"):
        classifier(on_timeout={"intent": "fallback"})


async def test_no_degrade_value_means_the_op_fails(hub):
    hub(inhouse=ScriptedLLM(completion('{"intent": "banana"}'), structured_output="native"))
    eng = engine(classifier())
    out = await eng.run(INPUTS)
    assert "value" not in out
    (error,) = out["$errors"].values()
    assert "OutputInvalid" in str(error)


async def test_cancelled_step_writes_nothing(hub):
    """A superseded step is cancelled before its answer: no outputs."""
    hub(
        inhouse=ScriptedLLM(completion('{"intent": "busy"}'), delay=0.5, structured_output="native")
    )
    eng = engine(classifier(model=Model("inhouse")))
    handle = eng.start(INPUTS)
    await asyncio.sleep(0.05)
    handle.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handle.result()
    assert not [n for n in handle.trace.nodes if n.op_name == "classify" and n.status == "ok"]


async def test_typed_model_output_and_messages_input(hub):
    class Callback(BaseModel):
        hour: int

    hub(m=ScriptedLLM(completion(json.dumps({"hour": 17})), structured_output="native"))
    step = llm_step(model=Model("m"), system="Extract the hour.", output=Callback)

    @graph
    def flow(messages):
        extract = step(messages=messages)
        START >> extract >> END

    out = await Operon(flow, params={"messages": None}).run(
        {"messages": [{"role": "user", "content": "call me at 5pm"}]}
    )
    assert out["value"] == Callback(hour=17)


async def test_missing_template_input_names_it(hub):
    hub(inhouse=ScriptedLLM(completion("{}"), structured_output="native"))
    out = await engine(classifier()).run({**INPUTS, "intent_prompt": None})
    (error,) = out["$errors"].values()
    assert "the template needs ['intent_prompt'], which arrived empty" in error["message"]
