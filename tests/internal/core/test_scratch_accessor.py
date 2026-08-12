"""``SCRATCH`` — the per-run scratchpad's read surface.

Marked 🟡 in the plan for months and probed rather than assumed. The write
path, cross-op reads, run isolation and the checkpointer bus all held. The
read surface did not: ``SCRATCH`` documents itself as "dict-like" and had
no ``get()``, so the idiom everyone reaches for first raised
``AttributeError`` — from inside an op body, where ``BaseOp.run`` records
it into state rather than raising, making a typo look like an op failure.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.core import END, PARENT, SCRATCH, START, GraphOp, Operon, op
from operonx.core.states.scratch_ref import ScratchRef

pytestmark = pytest.mark.unit


def _graph(reader_body):
    @op
    def writer(n: int) -> dict:
        SCRATCH["note"] = f"seen {n}"
        return {"ok": n}

    reader = op(reader_body)

    with GraphOp(name="scratch") as g:
        w = writer(n=PARENT["n"])
        r = reader(ok=w["ok"])
        START >> w >> r >> END
    return g


class TestReadSurface:
    @pytest.mark.asyncio
    async def test_get_returns_the_value(self):
        def reader(ok: int) -> dict:
            return {"seen": SCRATCH.get("note"), "ok": ok}

        out = await asyncio.wait_for(Operon(_graph(reader)).run(inputs={"n": 7}), timeout=20)
        assert out["seen"] == "seen 7"

    @pytest.mark.asyncio
    async def test_get_falls_back_to_the_default(self):
        def reader(ok: int) -> dict:
            return {"seen": SCRATCH.get("absent", "fallback"), "ok": ok}

        out = await asyncio.wait_for(Operon(_graph(reader)).run(inputs={"n": 1}), timeout=20)
        assert out["seen"] == "fallback"

    @pytest.mark.asyncio
    async def test_get_defaults_to_none(self):
        def reader(ok: int) -> dict:
            return {"seen": SCRATCH.get("absent"), "ok": ok}

        out = await asyncio.wait_for(Operon(_graph(reader)).run(inputs={"n": 1}), timeout=20)
        assert out["seen"] is None

    @pytest.mark.asyncio
    async def test_getitem_and_contains_still_work(self):
        def reader(ok: int) -> dict:
            return {"seen": SCRATCH["note"], "has": "note" in SCRATCH, "ok": ok}

        out = await asyncio.wait_for(Operon(_graph(reader)).run(inputs={"n": 3}), timeout=20)
        assert out["seen"] == "seen 3"
        assert out["has"] is True

    @pytest.mark.asyncio
    async def test_keys_and_items(self):
        def reader(ok: int) -> dict:
            return {"keys": list(SCRATCH.keys()), "items": list(SCRATCH.items()), "ok": ok}

        out = await asyncio.wait_for(Operon(_graph(reader)).run(inputs={"n": 5}), timeout=20)
        assert "note" in out["keys"]
        assert ("note", "seen 5") in out["items"]


class TestOutsideARun:
    def test_getitem_still_yields_a_wiring_ref(self):
        """``SCRATCH["k"]`` at construction time is a wiring marker, and
        must stay one — that is how it is threaded into an op's inputs."""
        assert isinstance(SCRATCH["anything"], ScratchRef)

    def test_get_returns_the_default_not_a_ref(self):
        """A ref smuggled into a value lookup would reach the op body as a
        marker object where data was expected."""
        assert SCRATCH.get("anything") is None
        assert SCRATCH.get("anything", "fallback") == "fallback"

    def test_keys_and_items_are_empty(self):
        assert tuple(SCRATCH.keys()) == ()
        assert tuple(SCRATCH.items()) == ()

    def test_contains_is_false(self):
        assert ("anything" in SCRATCH) is False


class TestRunIsolation:
    @pytest.mark.asyncio
    async def test_a_second_run_does_not_see_the_first(self):
        def reader(ok: int) -> dict:
            return {"seen": SCRATCH.get("note"), "ok": ok}

        engine = Operon(_graph(reader))
        first = await asyncio.wait_for(engine.run(inputs={"n": 1}), timeout=20)
        second = await asyncio.wait_for(engine.run(inputs={"n": 2}), timeout=20)
        assert first["seen"] == "seen 1"
        assert second["seen"] == "seen 2"


class TestObserverBus:
    @pytest.mark.asyncio
    async def test_the_checkpointer_records_scratch_writes(self):
        """Phase 2b3 B1 claimed this and nothing checked it."""
        from operonx.checkpoint import InMemoryCheckpointer

        def reader(ok: int) -> dict:
            return {"ok": ok}

        ck = InMemoryCheckpointer()
        handle = Operon(_graph(reader)).start(inputs={"n": 4}, checkpointer=ck)
        await asyncio.wait_for(handle.collect(), timeout=20)

        recorded = [
            key
            for step in ck.list_steps()
            for key in ck.get_updates(step)
            if key[0] == "__scratch__"
        ]
        assert ("__scratch__", "note", ("main",)) in recorded
