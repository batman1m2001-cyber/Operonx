"""Tests for SCRATCH primitive — per-call free-form key-value state.

Covers A1–A11 from docs/SCRATCH_PRIMITIVE_PLAN.md Phase A.
"""

import asyncio

import pytest

from operonx.core import (
    END,
    PARENT,
    SCRATCH,
    START,
    GraphOp,
    Operon,
    ScratchRef,
    op,
)
from operonx.core.ops._edges import ScratchAccessor
from operonx.core.ops._params import resolve_value
from operonx.core.states.schema import StateSchema
from operonx.core.states.state import MemoryState
from operonx.core.testing import scratch_active


# =============================================================================
# Shared ops (defined once, reused across tests)
# =============================================================================


@op
def write_then_signal(seed):
    SCRATCH["k"] = "wrote-" + seed
    return {"signal": True}


@op
def read_imperative(_signal):
    return {"result": SCRATCH["k"]}


@op
def read_missing(_signal):
    return {"result": SCRATCH["nope"]}


@op
def read_declarative(state):
    return {"result": state}


@op
def gen_three(_signal):
    for i in range(3):
        yield {"item": i, "scratch_k": SCRATCH["k"]}


@op
def consume_item(item, scratch_k):
    return {"out": (item, scratch_k)}


@op
async def spawn_via_create_task(_signal):
    async def _inner():
        await asyncio.sleep(0)
        return SCRATCH["k"]

    val = await asyncio.create_task(_inner())
    return {"result": val}


@op(bound="cpu")
def spawn_via_to_thread(_signal):
    # bound="cpu" runs the body via asyncio.to_thread, which uses
    # contextvars.copy_context() per invocation.
    return {"result": SCRATCH["k"]}


# =============================================================================
# A1: __slots__ extension + state.scratch property
# =============================================================================


class TestA1Slots:
    def test_state_scratch_initially_empty(self):
        with GraphOp(name="g") as g:
            n = read_missing(_signal=PARENT["seed"])
            START >> n >> END
        g.build()
        state = MemoryState(StateSchema(g))
        assert state._scratch == {}
        assert state.scratch is state._scratch

    def test_scratch_attribute_writable(self):
        """Adding _scratch to __slots__ allows attribute access without dict."""
        with GraphOp(name="g") as g:
            n = read_missing(_signal=PARENT["seed"])
            START >> n >> END
        g.build()
        state = MemoryState(StateSchema(g))
        state._scratch["abc"] = 1
        assert state._scratch == {"abc": 1}


# =============================================================================
# A2: Imperative write→read inside @op
# =============================================================================


class TestA2Imperative:
    @pytest.mark.asyncio
    async def test_write_in_one_op_read_in_downstream(self):
        with GraphOp(name="g") as g:
            w = write_then_signal(seed=PARENT["seed"])
            r = read_imperative(_signal=w["signal"])
            START >> w >> r >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "abc"})
        out = await h.collect(unwrap=True)
        assert out["result"] == "wrote-abc"


# =============================================================================
# A3: Missing key returns None (no KeyError)
# =============================================================================


class TestA3Missing:
    @pytest.mark.asyncio
    async def test_missing_key_returns_none(self):
        with GraphOp(name="g") as g:
            w = write_then_signal(seed=PARENT["seed"])
            r = read_missing(_signal=w["signal"])
            START >> w >> r >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "x"})
        out = await h.collect(unwrap=True)
        assert out["result"] is None


# =============================================================================
# A4: scratch_active() for unit tests (op body called directly)
# =============================================================================


class TestA4ScratchActive:
    def test_scratch_active_binds_state(self):
        with GraphOp(name="g") as g:
            n = read_missing(_signal=PARENT["seed"])
            START >> n >> END
        g.build()
        state = MemoryState(StateSchema(g))

        with scratch_active(state):
            SCRATCH["k"] = "via-test-helper"
            assert state.scratch == {"k": "via-test-helper"}
            assert SCRATCH["k"] == "via-test-helper"

        # ContextVar reset after exit — write outside should raise.
        with pytest.raises(RuntimeError):
            SCRATCH["leak"] = "x"


# =============================================================================
# A5: Declarative inputs={"x": SCRATCH["k"]} resolves per call
# =============================================================================


class TestA5Declarative:
    @pytest.mark.asyncio
    async def test_declarative_post_resolves_each_call(self):
        with GraphOp(name="g") as g:
            r = read_declarative(state=SCRATCH["k"])
            START >> r >> END

        engine = Operon(g)

        h1 = engine.start(inputs={}, scratch={"k": "v1"})
        out1 = await h1.collect(unwrap=True)
        assert out1["result"] == "v1"

        # Same engine, second call — fresh post-resolve.
        h2 = engine.start(inputs={}, scratch={"k": "v2"})
        out2 = await h2.collect(unwrap=True)
        assert out2["result"] == "v2"


# =============================================================================
# A5b: SCRATCH at module level returns marker (ContextVar unbound)
# =============================================================================


class TestA5bMarker:
    def test_module_level_returns_scratchref(self):
        marker = SCRATCH["coord:phase"]
        assert isinstance(marker, ScratchRef)
        assert marker.key == "coord:phase"

    def test_repr_is_helpful(self):
        assert repr(SCRATCH["x"]) == "SCRATCH['x']"

    def test_set_outside_run_raises(self):
        with pytest.raises(RuntimeError):
            SCRATCH["foo"] = 1

    def test_contains_outside_run_returns_false(self):
        assert ("any-key" in SCRATCH) is False


# =============================================================================
# A5c: _params.resolve_value preserves ScratchRef
# =============================================================================


class TestA5cParams:
    def test_resolve_value_returns_scratchref_unchanged(self):
        ref = ScratchRef("k")

        class _Parent:
            name = "parent"

        out = resolve_value("x", ref, _Parent())
        assert out is ref


# =============================================================================
# A5d: Schema _build() ignores ScratchRef defaults
# =============================================================================


class TestA5dSchema:
    def test_scratchref_default_not_converted_to_pull_ref(self):
        with GraphOp(name="g") as g:
            r = read_declarative(state=SCRATCH["k"])
            START >> r >> END
        g.build()
        schema = StateSchema(g)
        # Op name in schema follows the variable assignment (`r`), not the
        # function name (`read_declarative`).
        idx = schema.get_index("g.r", "state")
        assert idx >= 0
        assert isinstance(schema._defaults[idx], ScratchRef)
        assert schema._pull_refs[idx] is None


# =============================================================================
# A6: engine.start(scratch=...) seeds before first op
# =============================================================================


class TestA6Seed:
    @pytest.mark.asyncio
    async def test_seed_visible_to_entry_op(self):
        @op
        def entry_reader(seed):
            # Reads SCRATCH at the very first op — proves seeding is
            # synchronous, not racing the scheduler task.
            return {"result": SCRATCH["k"]}

        with GraphOp(name="g") as g:
            r = entry_reader(seed=PARENT["seed"])
            START >> r >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "ignored"}, scratch={"k": "from-seed"})
        out = await h.collect(unwrap=True)
        assert out["result"] == "from-seed"


# =============================================================================
# A7: handle.scratch external write/read
# =============================================================================


class TestA7HandleScratch:
    @pytest.mark.asyncio
    async def test_handle_scratch_synchronous_write_visible(self):
        @op
        def entry_reader(seed):
            return {"result": SCRATCH["k"]}

        with GraphOp(name="g") as g:
            r = entry_reader(seed=PARENT["seed"])
            START >> r >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "x"})
        # Synchronous write between start() and first await — race-free.
        h.scratch["k"] = "sync-write"
        out = await h.collect(unwrap=True)
        assert out["result"] == "sync-write"

    @pytest.mark.asyncio
    async def test_handle_scratch_read_after_run(self):
        with GraphOp(name="g") as g:
            w = write_then_signal(seed=PARENT["seed"])
            r = read_imperative(_signal=w["signal"])
            START >> w >> r >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "abc"})
        await h.collect(unwrap=True)
        assert h.scratch["k"] == "wrote-abc"


# =============================================================================
# A8: Multiple stream contexts share scratch
# =============================================================================


class TestA8StreamContexts:
    @pytest.mark.asyncio
    async def test_generator_items_share_scratch(self):
        with GraphOp(name="g") as g:
            w = write_then_signal(seed=PARENT["seed"])
            gen = gen_three(_signal=w["signal"])
            c = consume_item(item=gen["item"], scratch_k=gen["scratch_k"])
            START >> w >> gen >> c >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "abc"})
        out = await h.collect()
        # All three child contexts saw the same SCRATCH['k'] value.
        assert out["out"] == [(0, "wrote-abc"), (1, "wrote-abc"), (2, "wrote-abc")]


# =============================================================================
# A9: Concurrent engine.start() isolation
# =============================================================================


class TestA9Isolation:
    @pytest.mark.asyncio
    async def test_concurrent_calls_dont_cross_pollinate(self):
        with GraphOp(name="g") as g:
            w = write_then_signal(seed=PARENT["seed"])
            r = read_imperative(_signal=w["signal"])
            START >> w >> r >> END

        engine = Operon(g)
        N = 25

        async def one(i):
            h = engine.start(inputs={"seed": f"v{i}"})
            return await h.collect(unwrap=True)

        results = await asyncio.gather(*(one(i) for i in range(N)))
        for i, out in enumerate(results):
            assert out["result"] == f"wrote-v{i}", f"call {i}: got {out}"


# =============================================================================
# A10: ContextVar propagates through asyncio.create_task
# =============================================================================


class TestA10CreateTask:
    @pytest.mark.asyncio
    async def test_subtask_sees_parent_scratch(self):
        with GraphOp(name="g") as g:
            w = write_then_signal(seed=PARENT["seed"])
            r = spawn_via_create_task(_signal=w["signal"])
            START >> w >> r >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "abc"})
        out = await h.collect(unwrap=True)
        assert out["result"] == "wrote-abc"


# =============================================================================
# A11: ContextVar propagates through asyncio.to_thread (bound="cpu")
# =============================================================================


class TestA11ToThread:
    @pytest.mark.asyncio
    async def test_thread_op_sees_scratch(self):
        with GraphOp(name="g") as g:
            w = write_then_signal(seed=PARENT["seed"])
            r = spawn_via_to_thread(_signal=w["signal"])
            START >> w >> r >> END

        engine = Operon(g)
        h = engine.start(inputs={"seed": "abc"})
        out = await h.collect(unwrap=True)
        assert out["result"] == "wrote-abc"


# =============================================================================
# Sanity: SCRATCH accessor identity + module imports
# =============================================================================


class TestSCRATCHAccessor:
    def test_singleton_identity(self):
        # SCRATCH is a single ScratchAccessor instance.
        assert isinstance(SCRATCH, ScratchAccessor)

    def test_membership_inside_run(self):
        with GraphOp(name="g") as g:
            n = read_missing(_signal=PARENT["seed"])
            START >> n >> END
        g.build()
        state = MemoryState(StateSchema(g))
        with scratch_active(state):
            assert "k" not in SCRATCH
            SCRATCH["k"] = 1
            assert "k" in SCRATCH
            del SCRATCH["k"]
            assert "k" not in SCRATCH
