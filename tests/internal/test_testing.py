"""COST_PLAN round 2, C40: `operonx.testing.fake_model` scripts one model
for the length of a `with` block, then puts back what was there."""

import asyncio

import pytest

import operonx
from operonx import END, START, Operon, graph
from operonx.providers.ops import LLMOp
from operonx.testing import fake_model

pytestmark = pytest.mark.unit


@graph
def ask(q):
    s = LLMOp.of(resource="assistant", prompt="{q}", q=q)
    START >> s >> END


def _ask(q="hi"):
    return asyncio.run(Operon(ask, params={"q": None}).run(inputs={"q": q})).get("content")


@pytest.fixture
def hub(tmp_path):
    before = operonx.ResourceHub._instance
    p = tmp_path / "resources.yaml"
    p.write_text("llm:assistant:\n  api_type: fake\n  script:\n    - echo: 'Echo: '\n")
    operonx.bootstrap(resources=p, env=False)
    yield operonx.ResourceHub._instance
    operonx.ResourceHub.set_instance(before)


def test_the_block_answers_from_its_script_then_the_old_model_is_back(hub):
    assert _ask() == "Echo: hi"
    with fake_model({"status": 503}, "scripted!", retry={"max_attempts": 3, "initial": 0.01}) as m:
        assert _ask() == "scripted!"
        assert len(m.calls) == 2  # the 503, then the retry
    assert _ask() == "Echo: hi"


def test_a_key_the_hub_did_not_have_is_removed_after(hub):
    with fake_model("other", key="judge"):
        assert "llm:judge" in hub.keys()
    assert "llm:judge" not in hub.keys()


def test_with_no_hub_one_is_installed_for_the_block():
    before = operonx.ResourceHub._instance
    operonx.ResourceHub.set_instance(None)
    try:
        with fake_model("no hub needed"):
            assert _ask() == "no hub needed"
        assert operonx.ResourceHub._instance is None
    finally:
        operonx.ResourceHub.set_instance(before)


def test_an_engine_that_already_ran_answers_from_the_block(hub):
    # The template's session fixture warms one engine before any test runs; T9 of round 2 (both
    # tracks) opened `fake_model` after that and the op kept the model it had taken first.
    engine = Operon(ask, params={"q": None})
    run = lambda: asyncio.run(engine.run(inputs={"q": "hi"})).get("content")  # noqa: E731
    assert run() == "Echo: hi"
    with fake_model("from the block"):
        assert run() == "from the block"
    assert run() == "Echo: hi"
