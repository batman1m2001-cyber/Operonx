"""``Model``: fallback on 5xx and on refusal, none after the first streamed
delta, a deadline over the whole chain, and ``Usage`` normalised from
recorded responses."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
from openai.types.chat import ChatCompletion
from operonx import END, START, Operon, child, graph, op

from operonx_agents import Model, ModelError, ModelRefused, ModelSettings, ModelTimeout, Usage
from tests.fakes import ScriptedLLM, StatusError, chunk, completion

FIXTURES = Path(__file__).with_name("fixtures")
MSGS = [{"role": "user", "content": "hi"}]


class TestFallback:
    async def test_on_5xx(self, hub):
        primary = ScriptedLLM(StatusError(503))
        backup = ScriptedLLM(completion("from backup"))
        hub(a=primary, b=backup)
        reply = await Model("a", fallback=["b"]).request(MSGS)
        assert (reply.content, reply.model_used) == ("from backup", "b")
        assert primary.calls == backup.calls == 1

    async def test_transport_retry_on_the_same_resource_first(self, hub):
        """The resource's max_retries retries a 5xx there before falling back."""
        primary = ScriptedLLM(StatusError(502), completion("second try"), max_retries=2)
        backup = ScriptedLLM(completion("from backup"))
        hub(a=primary, b=backup)
        reply = await Model("a", fallback=["b"]).request(MSGS)
        assert reply.content == "second try" and backup.calls == 0
        assert reply.usage.requests == 1  # the failed attempt returned no usage

    async def test_4xx_is_not_retried_but_falls_back(self, hub):
        primary = ScriptedLLM(StatusError(400), completion("never"), max_retries=3)
        hub(a=primary, b=ScriptedLLM(completion("ok")))
        reply = await Model("a", fallback=["b"]).request(MSGS)
        assert primary.calls == 1 and reply.model_used == "b"

    async def test_on_refusal(self, hub):
        hub(
            a=ScriptedLLM(completion("", finish_reason="content_filter")),
            b=ScriptedLLM(completion("answered")),
        )
        reply = await Model("a", fallback=["b"]).request(MSGS)
        assert reply.content == "answered"
        assert reply.usage.requests == 2, "the refused request is still paid for"

    async def test_refusal_field_counts_as_refusal(self, hub):
        hub(a=ScriptedLLM(completion("", refusal="I can't")), b=ScriptedLLM(completion("ok")))
        assert (await Model("a", fallback=["b"]).request(MSGS)).model_used == "b"

    async def test_all_refuse(self, hub):
        hub(a=ScriptedLLM(completion("", finish_reason="safety")))
        with pytest.raises(ModelRefused, match="refused"):
            await Model("a").request(MSGS)

    async def test_all_fail_says_how_each_failed(self, hub):
        hub(a=ScriptedLLM(StatusError(500)), b=ScriptedLLM(ConnectionError("reset")))
        with pytest.raises(ModelError) as exc:
            await Model("a", fallback=["b"]).request(MSGS)
        assert [r for r, _ in exc.value.attempts] == ["a", "b"]
        assert "a: StatusError: HTTP 500" in str(exc.value) and "ConnectionError: reset" in str(
            exc.value
        )


class TestStreaming:
    async def test_no_fallback_after_the_first_delta(self, hub):
        primary = ScriptedLLM(stream_script=[chunk("Your appoint"), ConnectionError("dropped")])
        backup = ScriptedLLM(stream_script=[chunk("Monday."), chunk(finish_reason="stop")])
        hub(a=primary, b=backup)
        got = []
        with pytest.raises(ConnectionError):
            async for piece in Model("a", fallback=["b"]).stream(MSGS):
                got.append(piece)
        assert got == ["Your appoint"] and backup.calls == 0

    async def test_fallback_before_the_first_delta(self, hub):
        hub(
            a=ScriptedLLM(stream_script=[ConnectionError("refused")]),
            b=ScriptedLLM(stream_script=[chunk("Mon"), chunk("day."), chunk(finish_reason="stop")]),
        )
        got = [p async for p in Model("a", fallback=["b"]).stream(MSGS)]
        assert got[:2] == ["Mon", "day."]
        assert got[-1].content == "Monday." and got[-1].model_used == "b"

    async def test_a_stream_with_no_reply_is_a_failed_attempt(self, hub):
        """A gateway that answers a streamed request with nothing (no text, no
        tool call, no stop reason) failed: the next resource answers."""
        hub(
            a=ScriptedLLM(stream_script=[chunk()]),
            b=ScriptedLLM(stream_script=[chunk("Monday."), chunk(finish_reason="stop")]),
        )
        got = [p async for p in Model("a", fallback=["b"]).stream(MSGS)]
        assert got[-1].content == "Monday." and got[-1].model_used == "b"

    async def test_a_stream_with_no_reply_is_never_an_empty_answer(self, hub):
        hub(a=ScriptedLLM(stream_script=[chunk()]))
        with pytest.raises(ModelError, match="the stream ended with no reply"):
            async for _ in Model("a").stream(MSGS):
                pass


class TestDeadline:
    async def test_covers_the_whole_chain(self, hub):
        """The primary fails at 0.15 s and the fallback would answer at
        0.30 s: each fits a 0.2 s deadline alone, the chain does not."""
        backup = ScriptedLLM(completion("late"), delay=0.15)
        hub(a=ScriptedLLM(StatusError(503), delay=0.15), b=backup)
        start = time.perf_counter()
        with pytest.raises(ModelTimeout, match="0.2s deadline"):
            await Model("a", fallback=["b"], deadline=0.2).request(MSGS)
        elapsed = time.perf_counter() - start
        assert backup.calls == 1, "the fallback was reached and then cut"
        assert 0.2 <= elapsed < 0.22, elapsed

    async def test_is_a_timeout_error(self, hub):
        hub(a=ScriptedLLM(completion("late"), delay=1))
        with pytest.raises(TimeoutError):
            await Model("a", deadline=0.05).request(MSGS)

    async def test_no_deadline_waits(self, hub):
        hub(a=ScriptedLLM(completion("ok"), delay=0.05))
        assert (await Model("a").request(MSGS)).content == "ok"

    def test_bad_values(self):
        with pytest.raises(ValueError, match="without 'llm:'"):
            Model("llm:inhouse")
        with pytest.raises(ValueError, match="positive"):
            Model("a", deadline=0)


class TestUsage:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("inhouse", Usage(13, 2, 0, 0, 1, None)),
            ("qwen3.7-plus", Usage(16, 1, 0, 0, 1, None)),
        ],
    )
    async def test_from_recorded_openai_shaped_gateways(self, hub, name, expected):
        recorded = json.loads((FIXTURES / f"openai_shape_{name}.json").read_text())
        # construct, as the SDK does with a live body: no validation, so a
        # gateway's own values (service_tier "standard") pass as they came.
        hub(g=ScriptedLLM(ChatCompletion.construct(**recorded)))
        reply = await Model("g").request(MSGS)
        assert reply.usage == expected
        assert reply.logprobs, "the recording asked for logprobs"

    async def test_through_the_anthropic_backend(self, hub):
        """The backend's own conversion, fed an Anthropic response body."""
        from operonx.providers.llms.anthropic import AnthropicModel
        from operonx.providers.llms.config import LLMConfig

        body = json.loads((FIXTURES / "anthropic_messages.json").read_text())
        llm = AnthropicModel(
            LLMConfig.create_config(
                {
                    "api_type": "anthropic",
                    "api_key": "k",
                    "cost_per_input_token": 1e-6,
                    "cost_per_output_token": 5e-6,
                }
            )
        )
        llm.client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        )
        hub(claude=llm)
        reply = await Model("claude").request(MSGS)
        assert reply.usage == Usage(
            input_tokens=21 + 3000 + 1200,
            output_tokens=4,
            cached_input_tokens=3000,
            reasoning_tokens=0,
            requests=1,
            cost_usd=pytest.approx(4221 * 1e-6 + 4 * 5e-6),
        )

    def test_cost_is_none_when_unpriced(self):
        assert Usage(cost_usd=None) + Usage(cost_usd=1.0) == Usage(cost_usd=None)

    async def test_retries_and_fallbacks_add_up(self, hub):
        hub(
            a=ScriptedLLM(
                completion("", finish_reason="safety", prompt_tokens=7, completion_tokens=1)
            ),
            b=ScriptedLLM(completion("ok", prompt_tokens=5, completion_tokens=2)),
        )
        usage = (await Model("a", fallback=["b"]).request(MSGS)).usage
        assert (usage.input_tokens, usage.output_tokens, usage.requests) == (12, 3, 2)


FALLBACK = Model("a", fallback=["b"])


@op
async def ask_once(q: str) -> dict:
    return {"text": (await FALLBACK.request([{"role": "user", "content": q}])).content}


@graph
def asked(q):
    s = ask_once(q=q)
    START >> s >> END


@op
async def spoken(q: str) -> dict:
    text = ""
    async for piece in FALLBACK.stream([{"role": "user", "content": q}]):
        if isinstance(piece, str):
            async with child("speak", inputs={"text": piece}):
                text += piece
    return {"text": text}


@graph
def streamed(q):
    s = spoken(q=q)
    START >> s >> END


class TestRequest:
    async def test_settings_and_extras_reach_the_backend(self, hub):
        llm = ScriptedLLM(completion("ok"))
        llm.config.generation_extras = {"top_p": None, "reasoning_effort": "low", "temperature": 1}
        hub(a=llm)
        model = Model("a", settings=ModelSettings(max_tokens=16, logprobs=True, top_logprobs=2))
        await model.request(MSGS, settings=ModelSettings(temperature=0.5))
        sent = llm.requests[0]
        assert sent["temperature"] == 0.5, "the call wins over generation_extras"
        assert sent["max_tokens"] == 16 and sent["logprobs"] is True and sent["top_logprobs"] == 2
        assert sent["reasoning_effort"] == "low" and sent["top_p"] is None

    async def test_tool_calls_come_back_normalised(self, hub):
        hub(a=ScriptedLLM(completion(tool_calls=[{"id": "c1", "name": "f", "args": {"x": 1}}])))
        reply = await Model("a").request(
            MSGS, tools=[{"type": "function", "function": {"name": "f"}}]
        )
        assert reply.tool_calls == [{"id": "c1", "name": "f", "args": {"x": 1}}]

    async def test_each_resource_tried_is_a_child_execution(self, hub):
        hub(a=ScriptedLLM(StatusError(503)), b=ScriptedLLM(completion("ok")))
        handle = Operon(asked, params={"q": None}).start({"q": "hi"})
        assert (await handle.result())["text"] == "ok"
        calls = [n for n in handle.trace.nodes if n.op_type == "llm"]
        assert [(n.op_name, n.status) for n in calls] == [("model", "error"), ("model", "ok")]
        ok = calls[1]
        assert ok.attrs["gen_ai.operation.name"] == "chat" and ok.attrs["operonx.resource"] == "b"
        assert "cost_usd" in ok.outputs, "the key the run store counts LLM calls by"

    async def test_a_stream_records_each_resource_tried_and_its_consumer_stays_outside(self, hub):
        """The stream's record stays open across its yields; the consumer's
        own steps between the yields are its siblings, not its children."""
        hub(
            a=ScriptedLLM(stream_script=[ConnectionError("refused")]),
            b=ScriptedLLM(completion("Monday then Tuesday.")),
        )
        handle = Operon(streamed, params={"q": None}).start({"q": "hi"})
        assert (await handle.result())["text"] == "Monday then Tuesday."
        nodes = handle.trace.nodes
        calls = [n for n in nodes if n.op_type == "llm"]
        assert [(n.op_name, n.status) for n in calls] == [("model", "error"), ("model", "ok")]
        assert calls[1].outputs["content"] == "Monday then Tuesday."
        assert calls[1].outputs["usage"]["requests"] == 1
        assert calls[1].attrs["operonx.resource"] == "b"
        speaks = [n for n in nodes if n.op_name == "speak"]
        assert [n.ctx for n in speaks] == [("main", "speak[0]"), ("main", "speak[1]")]

    async def test_reasoning_streams_apart_from_the_answer(self, hub):
        from operonx_agents import Reasoning

        hub(a=ScriptedLLM(completion("42", reasoning="six times seven")))
        plain = [p async for p in Model("a").stream(MSGS)]
        assert plain[:-1] == ["4", "2"] and plain[-1].content == "42"
        pieces = [p async for p in Model("a").stream(MSGS, reasoning=True)]
        assert pieces[0] == Reasoning("six times seven") and pieces[-1].content == "42"


class TestTheHubItReads:
    async def test_a_model_made_once_reads_the_hub_installed_now(self, hub):
        """A project's agent is a module-level spec; each test (or a
        reloaded deployment) installs its own hub. The backend is the
        current hub's, not the first one's the model ever saw."""
        model = Model("m")
        hub(m=ScriptedLLM(completion("first")))
        assert (await model.request([{"role": "user", "content": "hi"}])).content == "first"
        hub(m=ScriptedLLM(completion("second")))
        assert (await model.request([{"role": "user", "content": "hi"}])).content == "second"
