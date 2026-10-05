"""``rate_limit:`` on a resource (ROADMAP §2 R1): one limiter per resource
key, shared by every op and every nested scheduler in the process."""

from __future__ import annotations

import asyncio
import time
from typing import ClassVar

import pytest

from operonx.core.registry import REGISTRY, ResourceHub
from operonx.core.registry.rate_limit import RateLimit, limit_instance
from operonx.core.utils.yaml_model import YamlModel

pytestmark = pytest.mark.unit


class ModelConfig(YamlModel):
    _category: ClassVar[str] = "ratemodel"
    name: str = "m"


class Model:
    def __init__(self, config):
        self.config = config
        self.running = 0
        self.peak = 0
        self.started: list = []

    async def generate(self, text: str) -> str:
        self.started.append(time.monotonic())
        self.running += 1
        self.peak = max(self.peak, self.running)
        await asyncio.sleep(0.05)
        self.running -= 1
        return text.upper()

    async def generate_batch(self, texts):
        # calls the limited method from inside a limited method
        return [await self.generate(t) for t in texts]

    async def stream(self, text: str):
        self.running += 1
        self.peak = max(self.peak, self.running)
        for ch in text:
            await asyncio.sleep(0.01)
            yield ch
        self.running -= 1

    def sync_name(self) -> str:
        return self.config.name


@pytest.fixture
def hub(tmp_path):
    saved = dict(REGISTRY._entries), dict(REGISTRY._class_entries)
    REGISTRY.register(ModelConfig, Model)
    path = tmp_path / "resources.yaml"
    path.write_text(
        "ratemodel:capped:\n"
        "  name: capped\n"
        "  rate_limit: {concurrency: 2, per_second: 20}\n"
        "ratemodel:free:\n"
        "  name: free\n"
    )
    try:
        yield ResourceHub.from_yaml(path)
    finally:
        REGISTRY._entries, REGISTRY._class_entries = saved


def test_concurrency_is_capped_across_callers(hub):
    model = hub.get("ratemodel:capped")

    async def go():
        return await asyncio.gather(*(model.generate(str(i)) for i in range(6)))

    assert asyncio.run(go()) == [str(i) for i in range(6)]
    assert model.peak == 2


def test_starts_are_spaced_by_the_rate(hub):
    model = hub.get("ratemodel:capped")

    async def go():
        await asyncio.gather(*(model.generate("x") for i in range(30)))

    t0 = time.monotonic()
    asyncio.run(go())
    # 20 per second: 30 calls cannot all start inside the first second
    assert time.monotonic() - t0 >= 0.45
    started = sorted(model.started)
    window = [t for t in started if t - started[0] < 1.0]
    assert len(window) <= 20


def test_a_limited_call_inside_a_limited_call_does_not_wait_on_itself(tmp_path):
    model = limit_instance(Model(ModelConfig()), RateLimit(concurrency=1), key="m")

    async def go():
        return await asyncio.wait_for(model.generate_batch(["a", "b"]), 5)

    assert asyncio.run(go()) == ["A", "B"]


def test_a_stream_holds_its_slot_until_it_ends(hub):
    model = hub.get("ratemodel:capped")

    async def read(text):
        return "".join([ch async for ch in model.stream(text)])

    async def go():
        return await asyncio.gather(*(read("abc") for _ in range(4)))

    assert asyncio.run(go()) == ["abc"] * 4
    assert model.peak == 2


def test_an_unlimited_resource_is_left_as_it_is(hub):
    model = hub.get("ratemodel:free")
    assert type(model).generate is Model.generate and "generate" not in vars(model)
    assert hub.get("ratemodel:capped").sync_name() == "capped"  # sync methods untouched


def test_one_limiter_per_resource_across_event_loops(hub):
    model = hub.get("ratemodel:capped")

    async def go():
        await asyncio.wait_for(asyncio.gather(*(model.generate("y") for _ in range(4))), 5)

    for _ in range(2):  # a fresh loop each time: no waiter bound to a dead loop
        asyncio.run(go())
    assert model.peak == 2


@pytest.mark.parametrize(
    "bad",
    [{"concurrency": 0}, {"per_second": -1}, {"per_minute": 1, "per_second": 1}, {"burst": 3}],
)
def test_a_rate_limit_it_cannot_read_is_refused(bad):
    with pytest.raises(ValueError, match="rate_limit"):
        RateLimit.parse(bad, key="k")
