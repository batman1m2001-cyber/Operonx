"""An op that raises is reported by the run, not raised out of it (S1).

A run still finishes when one op fails — one bad op must not end a live
call — but the failure is no longer invisible. ``run()``, ``collect()``
and ``result()`` carry ``"$errors"`` when at least one op raised, and the
handle carries the same dict as ``handle.errors``. Before, all three
returned a bare ``{}`` that nothing distinguished from an empty answer.
"""

import asyncio

import pytest

from operonx.checkpoint import ObserveBudgetExceeded
from operonx.core import END, PARENT, START, GraphOp, Operon, op
from operonx.core.ops.base import BaseOp


@op
def parse(x: str):
    return {"n": int(x)}


@op
async def aparse(x: str):
    return {"m": int(x)}


@op
def ok(x: str):
    return {"echo": x}


def _two_failing():
    with GraphOp(name="errs") as g:
        p = parse(x=PARENT["x"])  # sync: drained inline
        a = aparse(x=PARENT["x"])  # async: driven by the scheduler's _pump
        START >> [p, a]
        [p, a] >> END
    return g


class TestErrorsKey:
    async def test_run_reports_each_raising_op_with_its_error_cell_text(self):
        engine = Operon(_two_failing())
        out = await engine.run(inputs={"x": "not a number"})

        state = out["$state"]
        assert out["$errors"] == {
            f"{engine.name}.p": state[f"{engine.name}.p", "error"],
            f"{engine.name}.a": state[f"{engine.name}.a", "error"],
        }
        assert "ValueError" in out["$errors"][f"{engine.name}.p"]
        # Outputs of a failed op are still simply missing.
        assert "n" not in out and "m" not in out

    async def test_no_errors_key_when_nothing_failed(self):
        with GraphOp(name="clean") as g:
            k = ok(x=PARENT["x"])
            START >> k >> END
        engine = Operon(g)

        out = await engine.run(inputs={"x": "hi"})
        assert "$errors" not in out
        assert set(out) == {"echo", "$state"}

        handle = engine.start(inputs={"x": "hi"})
        assert await handle.result() == {"echo": "hi"}
        assert handle.errors == {}

    async def test_handle_errors_matches_collect_and_result(self):
        engine = Operon(_two_failing())

        handle = engine.start(inputs={"x": "nope"})
        collected = await handle.collect()
        assert set(handle.errors) == {f"{engine.name}.p", f"{engine.name}.a"}
        assert collected["$errors"] == handle.errors
        assert (await handle.result())["$errors"] == handle.errors

        unwrapped = await engine.start(inputs={"x": "nope"}).collect(unwrap=True)
        assert set(unwrapped["$errors"]) == set(handle.errors)

    async def test_the_errors_of_a_healthy_op_do_not_block_its_neighbour(self):
        @op
        def boom(x: str):
            raise RuntimeError("down")

        with GraphOp(name="mixed") as g:
            b = boom(x=PARENT["x"])
            k = ok(x=PARENT["x"])
            START >> [b, k]
            [b, k] >> END
        engine = Operon(g)

        out = await engine.run(inputs={"x": "hi"})
        assert out["echo"] == "hi"
        assert list(out["$errors"]) == [f"{engine.name}.b"]
        assert "RuntimeError: down" in out["$errors"][f"{engine.name}.b"]

    async def test_nested_op_is_keyed_by_its_full_path(self):
        with GraphOp(name="outer") as g:
            with GraphOp(name="inner") as sub:
                p = parse(x=PARENT["x"])
                START >> p >> END
            START >> sub >> END
        engine = Operon(g)

        out = await engine.run(inputs={"x": "nope"})
        assert list(out["$errors"]) == ["outer.inner.p"]
        assert out["$errors"]["outer.inner.p"] == out["$state"]["outer.inner.p", "error"]

    async def test_a_subgraph_failing_itself_is_reported(self, monkeypatch):
        """`GraphOp.run` has its own handler, for what fails around the
        children rather than inside one — it has to report the same way."""
        from operonx.core import GraphOp as _GraphOp

        real = _GraphOp.get_inputs

        def failing(self, state, context_id=None):
            if self.name == "inner":
                raise LookupError("subgraph inputs unreadable")
            return real(self, state, context_id)

        with GraphOp(name="outer") as g:
            with GraphOp(name="inner") as sub:
                k = ok(x=PARENT["x"])
                START >> k >> END
            START >> sub >> END
        engine = Operon(g)

        monkeypatch.setattr(_GraphOp, "get_inputs", failing)
        out = await engine.run(inputs={"x": "hi"})
        assert list(out["$errors"]) == ["outer.inner"]
        assert "LookupError: subgraph inputs unreadable" in out["$errors"]["outer.inner"]
        assert out["$errors"]["outer.inner"] == out["$state"]["outer.inner", "error"]

    async def test_an_op_failing_on_several_items_keeps_its_first_error(self):
        @op
        def items(n: int):
            for i in range(n):
                yield {"i": i}

        @op
        def picky(i: int):
            if i:
                raise ValueError(f"item {i}")
            return {"v": i}

        with GraphOp(name="fan") as g:
            s = items(n=PARENT["n"])
            p = picky(i=s["i"])
            START >> s >> p >> END
        engine = Operon(g)

        out = await engine.run(inputs={"n": 3})
        assert list(out["$errors"]) == [f"{engine.name}.p"]
        assert "item 1" in out["$errors"][f"{engine.name}.p"]


class TestWhatStillRaises:
    async def test_a_base_exception_still_reaches_the_caller(self):
        with GraphOp(name="fatal_g") as g:
            PARENT.declare(count=0)

            @op(observe_max=1)
            def burst():
                return {"a": 1, "b": 2}

            b = burst(name="burst")
            b["a"] >> PARENT["count"]
            START >> b >> END

        with pytest.raises(ObserveBudgetExceeded):
            await Operon(g).run(inputs={})

    async def test_a_failure_outside_the_op_body_is_raised_not_swallowed(self, monkeypatch):
        """Only the op body is the op's to fail. An exception escaping
        `op.run` itself is the framework failing, and it used to go through
        two handlers that could not handle it: FuncOp re-wrapped it as a
        CodeError, then `_pump` tried to write it to `state[local_name,
        "error"]`, which is not a key — so the task died with an
        unretrieved KeyError and the run returned as if nothing happened.
        """

        @op
        async def aok(x: int):
            return {"m": x + 1}

        with GraphOp(name="probe") as g:
            a = aok(x=PARENT["x"])
            START >> a >> END

        real = BaseOp._store_metrics

        def failing(self, *args, **kwargs):
            if self.name == "a":
                raise RuntimeError("escaped the op's own handler")
            return real(self, *args, **kwargs)

        monkeypatch.setattr(BaseOp, "_store_metrics", failing)
        with pytest.raises(RuntimeError, match="escaped the op's own handler"):
            await asyncio.wait_for(Operon(g).run(inputs={"x": 1}), 5)
