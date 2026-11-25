"""``decision:`` resources (TypeSafe Jev) and DecisionOp (docs/JEV_PLAN.md).

The wire is mocked: the body, the headers, both error shapes (TypeSafe's
``{"detail": ...}`` and OpenRouter's ``{"error": {...}}``, as probed on
2026-11-23), retries, the overflow split, cost, and the op in a graph.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from operonx import END, START, Operon, graph
from operonx.core.exceptions import DecisionError
from operonx.core.registry import ResourceHub
from operonx.providers.decisions import (
    DecisionConfig,
    DecisionType,
    create_decision,
    system_one,
    verdict,
)
from operonx.providers.decisions.base import normalize_answers, server_message
from operonx.providers.ops import DecisionOp
from operonx.telemetry.runs.model import row_of, summarize

pytestmark = pytest.mark.unit

QUESTIONS = {
    "refund": {"type": "noul", "instructions": "The customer asks for a refund."},
    "tone": {
        "type": "choice",
        "instructions": "The customer's tone.",
        "criteria": {"calm": None, "angry": None},
    },
}


def _answer(body):
    answers = {}
    for name, q in body["questions"].items():
        if q["type"] == "noul":
            answers[name] = {"type": "noul", "noul": 0.9}
        else:
            answers[name] = {
                "type": "choice",
                "choice": "angry",
                "confidence": 0.6,
                "probabilities": {"calm": 0.2, "angry": 0.8},
            }
    return answers


@pytest.fixture
def wire(monkeypatch):
    """Every request, answered by ``seen['reply']`` (a list, in order) or a 200."""
    seen = {"requests": [], "reply": [], "cost": 0.0001}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["requests"].append({"url": str(request.url), "headers": request.headers, "body": body})
        if seen["reply"]:
            reply = seen["reply"].pop(0)
            if callable(reply):
                return reply(body)
            return reply
        usage = {"input_tokens": 100, "output_tokens": 2}
        if seen["cost"] is not None:
            usage["cost"] = seen["cost"]
        return httpx.Response(
            200, json={"model": "typesafe/jev-1.14.0", "answers": _answer(body), "usage": usage}
        )

    monkeypatch.setattr(
        system_one,
        "create_http_client",
        lambda timeout=None: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return seen


def _config(**over):
    base = dict(api_type="openrouter", api_key="sk-test", model="typesafe/jev-1.14")
    base.update(over)
    base.setdefault("retry", {"max_attempts": 3, "initial": 0.0, "max_interval": 0.0})
    return DecisionConfig(**base)


def _decide(decider, questions=QUESTIONS, state="I want my money back!", assets=None):
    return asyncio.run(decider.decide(state, questions, assets))


class TestConfig:
    def test_routes_fill_the_base_url(self):
        assert _config().base_url == "https://openrouter.ai/api/v1"
        assert (
            _config(api_type="typesafe", model="jev-1.14.0").base_url
            == "https://api.typesafe.ai/v1"
        )

    def test_model_is_required_except_for_fake(self):
        with pytest.raises(ValueError, match="model"):
            DecisionConfig(api_type="openrouter", api_key="k")
        assert DecisionConfig(api_type="fake").api_type == DecisionType.FAKE

    def test_a_missing_key_names_the_env_var(self):
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            create_decision(_config(api_key=None))


class TestWire:
    def test_body_url_and_headers(self, wire):
        d = _decide(create_decision(_config(headers={"X-Title": "t"}), "jev"))
        req = wire["requests"][0]
        assert req["url"] == "https://openrouter.ai/api/v1/systemone"
        assert req["body"] == {
            "model": "typesafe/jev-1.14",
            "state": "I want my money back!",
            "questions": QUESTIONS,
        }
        assert req["headers"]["authorization"] == "Bearer sk-test"
        assert req["headers"]["x-title"] == "t"
        assert system_one.LONG_CONTEXT_HEADER.lower() not in req["headers"]
        assert d.model == "typesafe/jev-1.14.0"

    def test_answers_get_a_confidence_where_the_server_sent_none(self, wire):
        d = _decide(create_decision(_config(), "jev"))
        assert d.answers["refund"] == {
            "type": "noul",
            "noul": 0.9,
            "confidence": pytest.approx(0.8),
        }
        assert d.answers["tone"]["confidence"] == 0.6  # the server's own is kept

    def test_usage_has_the_llm_names(self, wire):
        d = _decide(create_decision(_config(), "jev"))
        assert d.usage == {"prompt_tokens": 100, "completion_tokens": 2, "total_tokens": 102}

    def test_long_context_and_images_send_their_headers(self, wire):
        _decide(
            create_decision(_config(long_context=True), "jev"), assets={"img": {"url": "data:,"}}
        )
        req = wire["requests"][0]
        assert req["headers"][system_one.LONG_CONTEXT_HEADER] == "true"
        assert req["headers"][system_one.IMAGES_HEADER] == "true"
        assert req["body"]["assets"] == {"img": {"url": "data:,"}}

    def test_a_dict_state_goes_as_json(self, wire):
        _decide(create_decision(_config(), "jev"), state={"ticket": 7, "text": "hi"})
        assert wire["requests"][0]["body"]["state"] == {"ticket": 7, "text": "hi"}


class TestCost:
    def test_the_reported_cost_wins(self, wire):
        assert _decide(create_decision(_config(cost_per_input_token=1.0), "jev")).cost_usd == 0.0001

    def test_a_price_when_nothing_is_reported(self, wire):
        wire["cost"] = None
        d = _decide(create_decision(_config(cost_per_input_token=4.2e-8), "jev"))
        assert d.cost_usd == pytest.approx(100 * 4.2e-8)

    def test_unknown_is_none_not_zero(self, wire):
        wire["cost"] = None
        assert _decide(create_decision(_config(), "jev")).cost_usd is None


class TestErrors:
    def test_openrouter_wrapped_422(self, wire):
        detail = {
            "detail": [
                {
                    "type": "missing",
                    "loc": ["body", "questions", "x", "type"],
                    "msg": "Field required",
                }
            ]
        }
        wire["reply"] = [
            httpx.Response(
                422, json={"error": {"message": f"HTTP 422: {json.dumps(detail)}", "code": 422}}
            )
        ]
        with pytest.raises(DecisionError) as e:
            _decide(create_decision(_config(), "jev"))
        assert e.value.status == 422
        assert "Field required" in str(e.value) and len(wire["requests"]) == 1  # not retried

    def test_typesafe_detail_list(self):
        body = {"detail": [{"loc": ["body", "questions", "x", "type"], "msg": "Field required"}]}
        assert server_message(json.dumps(body)) == "questions.x.type: Field required"

    def test_401_hints_at_the_key(self, wire):
        wire["reply"] = [httpx.Response(401, json={"error": {"message": "No auth", "code": 401}})]
        with pytest.raises(DecisionError, match="api_key"):
            _decide(create_decision(_config(), "jev"))

    def test_429_and_529_are_retried_honouring_retry_after(self, wire):
        wire["reply"] = [
            httpx.Response(429, headers={"Retry-After": "0"}, json={"detail": "slow down"}),
            httpx.Response(529, json={"detail": "overloaded"}),
        ]
        d = _decide(create_decision(_config(), "jev"))
        assert len(wire["requests"]) == 3 and d.answers["refund"]["noul"] == 0.9

    def test_a_200_page_that_is_not_an_answer_names_base_url(self, wire):
        wire["reply"] = [httpx.Response(200, json={"choices": []})]
        with pytest.raises(DecisionError, match="base_url"):
            _decide(create_decision(_config(), "jev"))

    def test_non_json_names_base_url(self, wire):
        wire["reply"] = [httpx.Response(200, text="<html>")]
        with pytest.raises(DecisionError, match="base_url"):
            _decide(create_decision(_config(), "jev"))

    def test_a_transport_error_stays_raw(self, wire):
        def boom(body):
            raise httpx.ConnectError("down")

        wire["reply"] = [boom, boom, boom]
        with pytest.raises(httpx.ConnectError):
            _decide(create_decision(_config(), "jev"))
        assert len(wire["requests"]) == 3

    @pytest.mark.parametrize("bad", [{}, [], {"x": "noul"}, {"x": {"instructions": "no type"}}])
    def test_questions_outline_is_checked_before_sending(self, wire, bad):
        with pytest.raises(DecisionError):
            _decide(create_decision(_config(), "jev"), questions=bad)
        assert wire["requests"] == []


OVERFLOW = {
    "error": {"message": 'HTTP 400: {"detail":{"error_type":"max_tokens_exceeded"}}', "code": 400}
}


class TestOverflow:
    def test_several_questions_split_in_halves_and_merge(self, wire):
        wire["reply"] = [httpx.Response(400, json=OVERFLOW)]
        d = _decide(create_decision(_config(), "jev"))
        assert set(d.answers) == {"refund", "tone"}
        assert [set(r["body"]["questions"]) for r in wire["requests"][1:]] in (
            [{"refund"}, {"tone"}],
            [{"tone"}, {"refund"}],
        )
        assert d.usage["prompt_tokens"] == 200 and d.cost_usd == pytest.approx(0.0002)

    def test_one_question_over_the_context_raises(self, wire):
        wire["reply"] = [httpx.Response(400, json=OVERFLOW)]
        with pytest.raises(DecisionError, match="max_tokens_exceeded"):
            _decide(create_decision(_config(), "jev"), questions={"refund": QUESTIONS["refund"]})

    def test_split_can_be_turned_off(self, wire):
        wire["reply"] = [httpx.Response(400, json=OVERFLOW)]
        with pytest.raises(DecisionError):
            _decide(create_decision(_config(split_on_overflow=False), "jev"))
        assert len(wire["requests"]) == 1


class TestFake:
    def test_script_order_defaults_and_status(self):
        cfg = DecisionConfig(
            api_type="fake", script=[{"refund": {"noul": 0.1}}, {"status": 429}, {}]
        )
        fake = create_decision(cfg, "jev")
        first = _decide(fake)
        assert first.answers["refund"]["noul"] == 0.1 and first.answers["refund"][
            "confidence"
        ] == pytest.approx(0.8)
        assert (
            first.answers["tone"]["choice"] == "calm" and first.answers["tone"]["confidence"] == 1.0
        )
        with pytest.raises(DecisionError) as e:
            _decide(fake)
        assert e.value.status == 429
        assert _decide(fake).answers["refund"]["noul"] == 0.5
        assert len(fake.calls) == 3


class TestVerdict:
    @pytest.mark.parametrize(
        "p,want", [(0.91, True), (0.7, True), (0.55, None), (0.3, False), (0.05, False)]
    )
    def test_noul(self, p, want):
        assert verdict({"type": "noul", "noul": p}) is want

    def test_choice(self):
        a = {"type": "choice", "choice": "angry", "probabilities": {"calm": 0.2, "angry": 0.8}}
        assert verdict(a) == "angry" and verdict(a, at=0.9) is None

    def test_score_and_bad_threshold_raise(self):
        with pytest.raises(ValueError):
            verdict({"type": "score", "score": 2})
        with pytest.raises(ValueError):
            verdict({"type": "noul", "noul": 0.9}, at=0.3)

    def test_normalize_refuses_missing_answers(self):
        with pytest.raises(DecisionError, match="base_url"):
            normalize_answers({"refund": {"noul": 0.5}}, QUESTIONS)


# ---------------------------------------------------------------- the op

RESOURCES = """
decision:jev:
  api_type: fake
  script:
    - {refund: {noul: 0.95}, tone: {choice: angry, probabilities: {calm: 0.1, angry: 0.9}}}
decision:other:
  api_type: fake
  script:
    - {refund: {noul: 0.02}}
decision:live:
  api_type: openrouter
  api_key: sk-test
  model: typesafe/jev-1.14
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
def triage(message):
    d = DecisionOp.of(resource="jev", state=message, questions=QUESTIONS)
    START >> d >> END


@graph
def priced():
    d = DecisionOp.of(resource="live", state="x", questions=QUESTIONS)
    START >> d >> END


@graph
def triage_on(model, message):
    d = DecisionOp.of(resource=model, state=message, questions=QUESTIONS)
    START >> d >> END


class TestOp:
    def test_in_a_graph(self, hub):
        out = asyncio.run(Operon(triage, params={"message": None}).run({"message": "refund now"}))
        assert out["answers"]["refund"]["noul"] == 0.95
        assert verdict(out["answers"]["tone"]) == "angry"
        assert out["cost_usd"] == 0.0 and out["model_used"] == "fake"
        assert hub.get("decision:jev").calls[0]["state"] == "refund now"

    def test_resource_at_run_time(self, hub):
        engine = Operon(triage_on, params={"model": None, "message": None})

        async def go():
            return [await engine.run({"model": m, "message": "x"}) for m in ("jev", "other")]

        a, b = asyncio.run(go())
        assert a["answers"]["refund"]["noul"] == 0.95 and b["answers"]["refund"]["noul"] == 0.02

    def test_an_unknown_resource_fails_at_warmup(self, hub):
        op = DecisionOp.of(resource="missing", state="x", questions=QUESTIONS)
        with pytest.raises(Exception, match="missing"):
            op.warmup()

    def test_serialize_leaves_the_key_out(self, hub):
        data = DecisionOp.of(resource="jev", state="x", questions=QUESTIONS).serialize()
        assert data["resource"] == "jev" and "api_key" not in data["resource_config"]

    def test_a_run_row_counts_it_as_a_priced_model_call(self, wire, hub):
        async def go():
            handle = Operon(priced).start(inputs={})
            await asyncio.wait_for(handle.collect(), timeout=20)
            return handle.trace

        trace = asyncio.run(go())
        summary, _ = summarize("t", [row_of(n, trace) for n in trace.nodes])
        assert summary.llm_calls == 1 and summary.tokens_in == 100
        assert summary.cost_usd == pytest.approx(0.0001)


# ---------------------------------------------------------------- self-hosted


def _envelope(body, status=200, problem=None):
    inner = {
        "answers": _answer(body["request"]),
        "usage": {"input_tokens": 50, "output_tokens": 1},
        "model": "glide-2b-v2",
    }
    return httpx.Response(
        200,
        json={"status": status, "ms": 12.5, "problem": problem, "req_id": "r1", "response": inner},
    )


def _run_config(**over):
    base = dict(api_type="run", base_url="https://jev.internal/api/run", model="glide-2b-v2")
    base.update(over)
    base.setdefault("retry", {"max_attempts": 3, "initial": 0.0, "max_interval": 0.0})
    return DecisionConfig(**base)


class TestRun:
    def test_the_envelope_goes_to_base_url_with_no_key(self, wire):
        wire["reply"] = [_envelope]
        d = _decide(create_decision(_run_config(), "glide"))
        req = wire["requests"][0]
        assert req["url"] == "https://jev.internal/api/run"
        assert req["body"] == {
            "model": "glide-2b-v2",
            "request": {"state": "I want my money back!", "questions": QUESTIONS},
        }
        assert "authorization" not in req["headers"]
        assert d.answers["refund"]["noul"] == 0.9 and d.answers["refund"][
            "confidence"
        ] == pytest.approx(0.8)
        assert d.usage["prompt_tokens"] == 50 and d.model == "glide-2b-v2" and d.cost_usd is None

    def test_a_key_is_sent_when_given(self, wire):
        wire["reply"] = [_envelope]
        _decide(create_decision(_run_config(api_key="k"), "glide"))
        assert wire["requests"][0]["headers"]["authorization"] == "Bearer k"

    def test_a_429_in_the_envelope_is_retried(self, wire):
        wire["reply"] = [lambda b: _envelope(b, status=429, problem="busy"), _envelope]
        d = _decide(create_decision(_run_config(), "glide"))
        assert len(wire["requests"]) == 2 and d.answers["refund"]["noul"] == 0.9

    def test_a_400_in_the_envelope_is_not_retried(self, wire):
        wire["reply"] = [lambda b: _envelope(b, status=400, problem="bad question")]
        with pytest.raises(DecisionError, match="bad question") as e:
            _decide(create_decision(_run_config(), "glide"))
        assert e.value.status == 400 and len(wire["requests"]) == 1

    def test_a_problem_with_status_200_raises(self, wire):
        wire["reply"] = [lambda b: _envelope(b, problem="model not loaded")]
        with pytest.raises(DecisionError, match="model not loaded"):
            _decide(create_decision(_run_config(), "glide"))

    def test_a_missing_answer_in_the_envelope_names_base_url(self, wire):
        wire["reply"] = [
            httpx.Response(200, json={"status": 200, "problem": None, "response": {"answers": {}}})
        ]
        with pytest.raises(DecisionError, match="base_url"):
            _decide(create_decision(_run_config(), "glide"))

    def test_assets_are_refused_before_sending(self, wire):
        with pytest.raises(DecisionError, match="assets"):
            _decide(create_decision(_run_config(), "glide"), assets={"img": {"url": "data:,"}})
        assert wire["requests"] == []

    def test_base_url_is_required(self):
        with pytest.raises(ValueError, match="base_url"):
            DecisionConfig(api_type="run", model="glide-2b-v2")


class TestPath:
    def test_a_system_one_body_at_another_url_needs_no_key(self, wire):
        cfg = _config(
            api_type="typesafe",
            api_key=None,
            model="sl-4b",
            base_url="http://10.0.0.7/v1",
            path="/decisions",
        )
        _decide(create_decision(cfg, "sl"))
        req = wire["requests"][0]
        assert req["url"] == "http://10.0.0.7/v1/decisions"
        assert req["body"] == {
            "model": "sl-4b",
            "state": "I want my money back!",
            "questions": QUESTIONS,
        }
        assert "authorization" not in req["headers"]

    def test_the_default_path_is_systemone(self):
        assert _config().url == "https://openrouter.ai/api/v1/systemone"
        assert _config(base_url="https://openrouter.ai/api/v1/", path="decisions").url == (
            "https://openrouter.ai/api/v1/decisions"
        )

    def test_typesafes_own_host_still_needs_a_key(self):
        with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
            create_decision(_config(api_type="typesafe", api_key=None, model="jev-1.14.0"))


RUN_RESOURCES = """
decision:glide:
  api_type: run
  base_url: https://jev.internal/api/run
  model: glide-2b-v2
"""


def test_a_run_resource_in_a_graph(wire, tmp_path):
    path = tmp_path / "resources.yaml"
    path.write_text(RUN_RESOURCES)
    saved = ResourceHub._instance
    ResourceHub.set_instance(ResourceHub.from_yaml(path))
    try:

        @graph
        def ask(message):
            d = DecisionOp.of(resource="glide", state=message, questions=QUESTIONS)
            START >> d >> END

        wire["reply"] = [_envelope]
        out = asyncio.run(Operon(ask, params={"message": None}).run({"message": "refund now"}))
    finally:
        ResourceHub._instance = saved
    assert out["answers"]["refund"]["noul"] == 0.9 and out["model_used"] == "glide-2b-v2"
    assert wire["requests"][0]["body"]["request"]["state"] == "refund now"
