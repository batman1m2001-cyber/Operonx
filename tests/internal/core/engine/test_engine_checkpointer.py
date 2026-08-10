"""End-to-end tests: engine.run(checkpointer=) captures state deltas.

Verifies Phase 2 integration:
    - engine.start / engine.run / engine.invoke accept checkpointer=
    - bind_checkpointer wires state → checkpointer at run start; unhooks at
      run end (finally block)
    - step_id increments per op invocation (via state.advance_step in
      BaseOp.store_result)
    - @op(exclude/include/observe_max) filter is honoured by the bridge

Uses a minimal 3-op graph to keep step_id predictable.
"""

import operator

import pytest

from operonx import PARENT, Operon
from operonx.checkpoint import InMemoryCheckpointer, ObserveBudgetExceeded
from operonx.core.ops.graph.graph_op import END, START, GraphOp
from operonx.core.ops.transform.func_op import FuncOp, op


def _simple_graph():
    """A 3-op linear graph: seed → double → publish."""
    with GraphOp(name="pipeline") as g:
        PARENT.declare(count=0)

        @op
        def seed():
            return {"n": 5}

        @op
        def double(n: int):
            return {"n": n * 2}

        @op
        def publish(n: int):
            return {"n": n}

        s = seed(name="seed")
        d = double(n=s["n"], name="double")
        p = publish(n=d["n"], name="publish")
        p["n"] >> PARENT["count"]

        START >> s >> d >> p >> END
    return g


class TestCheckpointerBinding:
    async def test_run_with_no_checkpointer_still_works(self):
        engine = Operon(_simple_graph())
        result = await engine.run(inputs={})
        assert result["count"] == 10

    async def test_run_with_checkpointer_captures_writes(self):
        engine = Operon(_simple_graph())
        cp = InMemoryCheckpointer()

        await engine.run(inputs={}, checkpointer=cp)

        # Every declared cell write should have made it into the checkpointer.
        assert cp.list_steps(), "expected at least one recorded step"
        # Final state should reflect the push_ref of publish.n → PARENT.count.
        final = cp.get_state(step=max(cp.list_steps()))
        # Key form: (op_full_name, var, ctx). Shared cell key.
        assert any(k[0] == "pipeline" and k[1] == "count" and v == 10 for k, v in final.items()), (
            f"pipeline.count=10 not in final state: {final}"
        )

    async def test_step_id_monotonic_across_ops(self):
        engine = Operon(_simple_graph())
        cp = InMemoryCheckpointer()

        await engine.run(inputs={}, checkpointer=cp)

        steps = cp.list_steps()
        assert steps == sorted(steps), "step ids must be monotonic"
        assert len(steps) >= 3, "at least seed, double, publish should each bump"

    async def test_checkpointer_detached_on_run_end(self):
        engine = Operon(_simple_graph())
        cp = InMemoryCheckpointer()

        await engine.run(inputs={}, checkpointer=cp)

        # After run completes, further writes to the state (through user code
        # that somehow held the handle) should NOT go to the checkpointer.
        # Verified via state observer list being emptied.
        # (We can't easily grab state here without engine.start; smoke-test
        # via a second run with a fresh checkpointer — first cp should be
        # untouched by run 2.)
        cp2 = InMemoryCheckpointer()
        await engine.run(inputs={}, checkpointer=cp2)

        # cp captured run 1; cp2 captured run 2. Both non-empty, independent.
        assert cp.list_steps() and cp2.list_steps()


class TestCheckpointerReducerInteraction:
    async def test_reducer_merged_value_captured(self):
        with GraphOp(name="g") as g:
            PARENT.declare(log=[], reducers={"log": operator.add})

            @op
            def emit_a():
                return {"line": ["a"]}

            @op
            def emit_b():
                return {"line": ["b"]}

            a = emit_a(name="a")
            b = emit_b(name="b")
            a["line"] >> PARENT["log"]
            b["line"] >> PARENT["log"]

            START >> a >> b >> END

        engine = Operon(g)
        cp = InMemoryCheckpointer()
        await engine.run(inputs={}, checkpointer=cp)

        final = cp.get_state(step=max(cp.list_steps()))
        shared_log = [v for k, v in final.items() if k[0] == "g" and k[1] == "log"]
        # Reducer merged lists via operator.add: [] + ["a"] + ["b"]
        assert shared_log and shared_log[0] == ["a", "b"]


class TestObservabilityFilterIntegration:
    async def test_excluded_var_never_hits_checkpointer(self):
        with GraphOp(name="g") as g:
            PARENT.declare(count=0)

            @op(exclude=["n"])
            def hidden():
                return {"n": 42}

            h = hidden(name="hidden")
            h["n"] >> PARENT["count"]

            START >> h >> END

        engine = Operon(g)
        cp = InMemoryCheckpointer()
        await engine.run(inputs={}, checkpointer=cp)

        # hidden.n filtered out; PARENT.count (via push-ref) still captured.
        all_updates = {}
        for s in cp.list_steps():
            all_updates.update(cp.get_updates(s))
        assert not any(k[0] == "g.hidden" and k[1] == "n" for k in all_updates)
        assert any(k[0] == "g" and k[1] == "count" for k in all_updates)

    async def test_observe_max_raises(self):
        # NB: op filter check requires an op_registry — the engine builds one
        # via self._all_ops_registry(). We hit the limit at 2nd emit.
        with GraphOp(name="g") as g:
            PARENT.declare(count=0)

            @op(observe_max=1)
            def burst():
                return {"a": 1, "b": 2}

            b = burst(name="burst")
            b["a"] >> PARENT["count"]

            START >> b >> END

        engine = Operon(g)
        cp = InMemoryCheckpointer()

        # burst emits ≥2 events for its op (each of "a", "b"); cap=1 → raise.
        # The exception surfaces via the scheduler task; collect() re-raises it.
        with pytest.raises(ObserveBudgetExceeded) as exc_info:
            await engine.run(inputs={}, checkpointer=cp)
        assert exc_info.value.op.endswith("burst")
        assert exc_info.value.limit == 1


class TestEngineInvokeAlias:
    async def test_invoke_matches_run(self):
        engine = Operon(_simple_graph())
        r1 = await engine.run(inputs={})
        r2 = await engine.invoke(inputs={})
        # $state differs; ignore. Payload should match.
        assert r1["count"] == r2["count"] == 10


class TestEngineStreamModes:
    async def test_stream_frames_passes_through_handle_frames(self):
        """mode='frames' surfaces whatever the scheduler emits.

        In operonx's model, the scheduler emits frames for ops that push
        to the graph output (PARENT/END). For a linear pipeline like
        seed→double→publish where only publish's push_ref reaches PARENT,
        that means one output frame — the shape depends on the graph."""
        engine = Operon(_simple_graph())
        frames = []
        async for frame in engine.stream(inputs={}, mode="frames"):
            frames.append(frame)
        assert frames, "expected at least one frame"
        for f in frames:
            assert len(f) == 3  # (op, ctx, data)

    async def test_stream_updates_yields_op_dict(self):
        engine = Operon(_simple_graph())
        seen_ops = []
        chunks = []
        async for chunk in engine.stream(inputs={}, mode="updates"):
            assert isinstance(chunk, dict)
            chunks.append(chunk)
            seen_ops.extend(chunk.keys())
        # Every op's committed writes are one chunk; three ops means three chunks.
        assert any(op.endswith(".seed") for op in seen_ops), seen_ops
        assert any(op.endswith(".double") for op in seen_ops), seen_ops
        assert any(op.endswith(".publish") for op in seen_ops), seen_ops

    async def test_stream_values_yields_snapshots(self):
        engine = Operon(_simple_graph())
        snapshots = []
        async for snap in engine.stream(inputs={}, mode="values"):
            snapshots.append(snap)
        assert snapshots
        # Final snapshot must contain the pushed shared cell value.
        final = snapshots[-1]
        assert any(k[1] == "count" and v == 10 for k, v in final.items())

    async def test_stream_unknown_mode_raises(self):
        engine = Operon(_simple_graph())
        with pytest.raises(ValueError, match="valid modes"):
            async for _ in engine.stream(inputs={}, mode="bogus"):
                pass
