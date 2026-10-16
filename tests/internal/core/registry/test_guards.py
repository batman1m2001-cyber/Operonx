"""Guards on resources (``ResourceHub.guard``): a check before every call of a resource, whichever op makes it;
raising refuses the call before it starts."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from operonx.core.registry import REGISTRY, ResourceHub
from operonx.core.registry.guards import guards_for
from operonx.core.utils.yaml_model import YamlModel

pytestmark = pytest.mark.unit


class ModelConfig(YamlModel):
    _category: ClassVar[str] = "guardmodel"
    name: str = "m"


class Model:
    def __init__(self, config):
        self.config = config
        self.sent: list = []

    async def generate(self, messages, **kwargs) -> str:
        self.sent.append(messages)
        return "ok"

    async def stream(self, messages, **kwargs):
        self.sent.append(messages)
        for ch in "ok":
            yield ch

    def sync_name(self) -> str:
        return self.config.name


class Refused(PermissionError):
    pass


def no_secret(key, method, args, kwargs):
    if "secret" in str(kwargs.get("messages", args)):
        raise Refused(f"{key}.{method}: a secret")


@pytest.fixture
def hub(tmp_path):
    saved = dict(REGISTRY._entries), dict(REGISTRY._class_entries)
    previous = ResourceHub._instance
    REGISTRY.register(ModelConfig, Model)
    path = tmp_path / "resources.yaml"
    path.write_text(
        "guardmodel:outside:\n  name: outside\n  rate_limit: {concurrency: 1}\n"
        "guardmodel:inside:\n  name: inside\n"
        "other:thing:\n  name: thing\n"
    )
    hub = ResourceHub.from_yaml(path)
    ResourceHub.set_instance(hub)
    removers = []
    try:
        yield hub, removers
    finally:
        for remove in removers:
            remove()
        ResourceHub._instance = previous
        REGISTRY._entries, REGISTRY._class_entries = saved


def test_a_guard_refuses_a_call_before_it_is_made_and_lets_the_others_through(hub):
    hub, removers = hub
    removers.append(ResourceHub.guard("guardmodel:outside", no_secret))
    model = hub.get("guardmodel:outside")
    assert asyncio.run(model.generate(messages="hello")) == "ok"
    with pytest.raises(Refused, match="guardmodel:outside.generate"):
        asyncio.run(model.generate(messages="the secret"))
    assert model.sent == ["hello"]  # the refused call never reached the resource


def test_a_stream_is_checked_before_its_first_item(hub):
    hub, removers = hub
    removers.append(ResourceHub.guard("guardmodel:", no_secret))
    model = hub.get("guardmodel:outside")

    async def read(text):
        return "".join([ch async for ch in model.stream(text)])

    assert asyncio.run(read("hello")) == "ok"
    with pytest.raises(Refused):
        asyncio.run(read("a secret"))
    assert model.sent == ["hello"]


def test_only_resources_under_the_prefix_are_wrapped(hub):
    hub, removers = hub
    removers.append(ResourceHub.guard("guardmodel:outside", no_secret))
    inside = hub.get("guardmodel:inside")
    assert "generate" not in vars(inside)  # left exactly as it was
    assert asyncio.run(inside.generate(messages="a secret")) == "ok"
    assert hub.get("guardmodel:outside").sync_name() == "outside"  # sync methods untouched


def test_a_guard_added_later_covers_instances_already_handed_out(hub):
    hub, removers = hub
    model = hub.get("guardmodel:inside")
    assert asyncio.run(model.generate(messages="a secret")) == "ok"
    removers.append(ResourceHub.guard("guardmodel:", no_secret))
    with pytest.raises(Refused):
        asyncio.run(model.generate(messages="a secret"))
    # a second guard: wrapped once, both run
    removers.append(ResourceHub.guard("guardmodel:", lambda *a: None))
    with pytest.raises(Refused):
        asyncio.run(model.generate(messages="secret again"))


def test_guards_run_in_order_may_be_async_and_can_be_removed(hub):
    hub, removers = hub
    seen = []

    async def first(key, method, args, kwargs):
        seen.append(("first", key, method))

    def second(key, method, args, kwargs):
        seen.append(("second", key, method))

    remove = ResourceHub.guard("guardmodel:", first)
    removers.append(ResourceHub.guard("guardmodel:inside", second))
    model = hub.get("guardmodel:inside")
    asyncio.run(model.generate(messages="x"))
    assert seen == [
        ("first", "guardmodel:inside", "generate"),
        ("second", "guardmodel:inside", "generate"),
    ]
    remove()
    assert guards_for("guardmodel:inside") == [second]


def test_a_refused_call_takes_no_rate_limit_slot(hub):
    hub, removers = hub
    removers.append(ResourceHub.guard("guardmodel:outside", no_secret))
    model = hub.get("guardmodel:outside")  # concurrency 1

    async def go():
        for _ in range(3):
            with pytest.raises(Refused):
                await model.generate(messages="secret")
        return await asyncio.wait_for(model.generate(messages="fine"), 2)

    assert asyncio.run(go()) == "ok"


def test_a_guard_must_be_callable():
    with pytest.raises(TypeError):
        ResourceHub.guard("llm:", "not a function")


def test_an_llm_op_in_a_graph_is_refused_by_the_guard_and_nothing_is_sent(tmp_path):
    from operonx import END, START, Operon, graph
    from operonx.providers.ops import LLMOp

    path = tmp_path / "resources.yaml"
    path.write_text('llm:bot:\n  api_type: fake\n  script: ["fine"]\n')
    previous = ResourceHub._instance
    ResourceHub.set_instance(ResourceHub.from_yaml(path))

    def check(key, method, args, kwargs):
        messages = kwargs.get("messages") if "messages" in kwargs else (args[0] if args else [])
        if any("secret" in str(m.get("content")) for m in messages):
            raise Refused(f"{key}: a secret")

    @graph
    def ask(q):
        llm = LLMOp.of(resource="bot", prompt="{q}", q=q)
        START >> llm >> END

    remove = ResourceHub.guard("llm:", check)
    try:
        engine = Operon(ask, params={"q": None})
        ok = asyncio.run(engine.run({"q": "hello"}))
        refused = asyncio.run(engine.run({"q": "my secret"}))
        assert ok["content"] == "fine"
        assert "Refused" in str(refused["$errors"]) and "a secret" in str(refused["$errors"])
        assert [m[-1]["content"] for m in ResourceHub.instance().get("llm:bot").calls] == ["hello"]
    finally:
        remove()
        ResourceHub._instance = previous
