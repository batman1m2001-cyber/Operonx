"""Tests for PARENT.shared() — persistent vars across stream contexts."""

import asyncio

import pytest

from operonx.core import END, PARENT, START, GraphOp, op
from operonx.core.states import StateSchema


class TestSharedVarsBasic:
    """Basic shared var read/write."""

    @pytest.mark.asyncio
    async def test_shared_var_persists_across_yields(self):
        """Generator yields 3 items, each increments shared counter."""

        @op
        def source(n: int):
            for i in range(n):
                yield {"idx": i}

        @op
        def increment(idx: int, counter: int):
            return {"new_counter": counter + 1, "seen_idx": idx}

        with GraphOp(name="shared_test") as g:
            PARENT.shared(counter=0)

            s = source(n=PARENT["n"])
            inc = increment(idx=s["idx"], counter=PARENT["counter"])
            inc["new_counter"] >> PARENT["counter"]
            START >> s >> inc >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 3})
        result = {}
        async for _, result in g.run(state):
            pass

        # Counter incremented 3 times: 0→1→2→3
        # Shared var at DEFAULT_CONTEXT = final value (scalar via all contexts)
        counter_val = state[g.full_name, "counter", ("main",)]
        # Output collection returns same value for each context
        if isinstance(counter_val, list):
            assert counter_val[-1] == 3
        else:
            assert counter_val == 3

    @pytest.mark.asyncio
    async def test_shared_var_initial_value(self):
        """Shared var has correct initial value."""

        @op
        def source():
            yield {"x": 1}

        @op
        def reader(x: int, status: str):
            return {"result": f"{status}_{x}"}

        with GraphOp(name="init_test") as g:
            PARENT.shared(status="READY")

            s = source()
            r = reader(x=s["x"], status=PARENT["status"])
            START >> s >> r >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={})
        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)

        assert result["result"] == ["READY_1"]

    @pytest.mark.asyncio
    async def test_shared_var_updated_by_earlier_context(self):
        """Turn 1 writes shared var, Turn 2 reads updated value.

        Simulates multi-turn callbot: state changes between VAD segments.
        """

        @op
        def source(n: int):
            for i in range(n):
                yield {"turn": i}

        @op
        def process_turn(turn: int, state_val: str):
            new_state = f"state_{turn}"
            return {"result": f"read_{state_val}", "new_state": new_state}

        with GraphOp(name="multi_turn") as g:
            PARENT.shared(state_val="INITIAL")

            s = source(n=PARENT["n"])
            p = process_turn(turn=s["turn"], state_val=PARENT["state_val"])
            p["new_state"] >> PARENT["state_val"]
            START >> s >> p >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 3})
        result = {}
        async for _, result in g.run(state):
            pass

        # Final shared state after 3 turns
        final_state = state[g.full_name, "state_val", ("main",)]
        if isinstance(final_state, list):
            final_state = final_state[-1]
        assert final_state == "state_2"


class TestSharedVarsWithNormalVars:
    """Shared vars coexist with normal (per-context) vars."""

    @pytest.mark.asyncio
    async def test_mixed_shared_and_normal(self):
        """Shared var shared, normal var copied per context."""

        @op
        def source(n: int):
            for i in range(n):
                yield {"x": i}

        @op
        def process(x: int, shared_count: int):
            return {"result": x * 10, "new_count": shared_count + 1}

        with GraphOp(name="mixed") as g:
            PARENT.shared(shared_count=0)

            s = source(n=PARENT["n"])
            p = process(x=s["x"], shared_count=PARENT["shared_count"])
            p["new_count"] >> PARENT["shared_count"]
            START >> s >> p >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 3})
        result = {}
        async for _, result in g.run(state):
            pass

        # shared count incremented 3 times
        shared_count = state[g.full_name, "shared_count", ("main",)]
        if isinstance(shared_count, list):
            shared_count = shared_count[-1]
        assert shared_count == 3


class TestSharedVarsEdgeCases:
    """Edge cases."""

    @pytest.mark.asyncio
    async def test_no_shared_vars_still_works(self):
        """Graph without PARENT.shared() works as before."""

        @op
        def source(n: int):
            for i in range(n):
                yield {"x": i}

        @op
        def double(x: int):
            return {"result": x * 2}

        with GraphOp(name="no_shared") as g:
            s = source(n=PARENT["n"])
            d = double(x=s["x"])
            START >> s >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 3})
        result = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                result.setdefault(_k, []).append(_v)

        assert result["result"] == [0, 2, 4]

    @pytest.mark.asyncio
    async def test_shared_var_with_list_accumulation(self):
        """Shared list accumulates across turns (like conversation_history)."""

        @op
        def source(n: int):
            for i in range(n):
                yield {"msg": f"turn_{i}"}

        @op
        def append_history(msg: str, history: list):
            new_history = history + [msg]
            return {"new_history": new_history, "current_len": len(new_history)}

        with GraphOp(name="history") as g:
            PARENT.shared(history=[])

            s = source(n=PARENT["n"])
            a = append_history(msg=s["msg"], history=PARENT["history"])
            a["new_history"] >> PARENT["history"]
            START >> s >> a >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 3})
        result = {}
        async for _, result in g.run(state):
            pass

        # History accumulates: [] → ["turn_0"] → ["turn_0","turn_1"] → ["turn_0","turn_1","turn_2"]
        history = state[g.full_name, "history", ("main",)]
        if isinstance(history, list) and history and isinstance(history[0], list):
            history = history[-1]  # unwrap output collection list
        assert history == ["turn_0", "turn_1", "turn_2"]

    @pytest.mark.asyncio
    async def test_shared_var_output_is_scalar_not_list(self):
        """Shared var in graph output must be scalar, not wrapped in list.

        Bug regression: output collection was reading shared vars once per
        terminal stream context → wrapping in list [val, val, val].
        Must read once from DEFAULT_CONTEXT → scalar.
        """

        @op
        def source(n: int):
            for i in range(n):
                yield {"x": i}

        @op
        def accumulate(x: int, buffer: list):
            new_buffer = list(buffer) + [x]
            return {"new_buffer": new_buffer}

        with GraphOp(name="scalar_test") as g:
            PARENT.shared(buffer=[])

            s = source(n=PARENT["n"])
            a = accumulate(x=s["x"], buffer=PARENT["buffer"])
            a["new_buffer"] >> PARENT["buffer"]
            START >> s >> a >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 3})
        result = {}
        async for _, result in g.run(state):
            pass

        # Shared var: last frame has the final accumulated buffer
        assert result["buffer"] == [0, 1, 2]
        assert not isinstance(result["buffer"][0], list)

    @pytest.mark.asyncio
    async def test_shared_and_nonshared_output_types(self):
        """Shared vars = scalar in output, non-shared vars = list per context."""

        @op
        def source(n: int):
            for i in range(n):
                yield {"x": i}

        @op
        def process(x: int, counter: int):
            return {"result": x * 10, "new_counter": counter + 1}

        with GraphOp(name="mixed_output") as g:
            PARENT.shared(counter=0)

            s = source(n=PARENT["n"])
            p = process(x=s["x"], counter=PARENT["counter"])
            p["new_counter"] >> PARENT["counter"]
            p["result"] >> PARENT["result"]
            START >> s >> p >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 3})
        # Collect non-shared vars as list; read shared var from state directly
        collected = {}
        async for _, _frame in g.run(state):
            for _k, _v in _frame.items():
                collected.setdefault(_k, []).append(_v)

        # counter: shared → final value from state
        counter_val = state[g.full_name, "counter", ("main",)]
        assert counter_val == 3
        assert isinstance(counter_val, int)

        # result: non-shared → list per context
        assert collected["result"] == [0, 10, 20]
        assert isinstance(collected["result"], list)

    @pytest.mark.asyncio
    async def test_shared_only_on_parent(self):
        """PARENT.shared() raises if called on non-PARENT."""
        from operonx.core import START as s

        with pytest.raises(TypeError, match="shared.*PARENT"):
            s.shared(x=1)
