"""F3 — ``@op(exclude=…)`` did not filter the V3 trace.

``base.py`` documented the filter as "Checkpointer + Tracer both respect
these". Only the checkpoint, custom and interrupt buses ever consulted it;
``OpExecution`` recorded ``dict(_inputs)`` and ``dict(result)`` verbatim.
So the one documented way to keep a credential out of an observable
artifact excluded it from the durable log and printed it in the trace —
and in everything built from the trace, which is every consumer.

These assert on the trace's contents rather than on the predicate: the
predicate was always correct, it just had no caller here.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon, op

pytestmark = pytest.mark.unit

SECRET_IN = "sk-SECRET-INPUT"
SECRET_OUT = "tok-SECRET-OUTPUT"


async def _trace_of(node_factory, name):
    with GraphOp(name=name) as g:
        node = node_factory(api_key=PARENT["api_key"])
        START >> node >> END
    handle = Operon(g).start(inputs={"api_key": SECRET_IN})
    await asyncio.wait_for(handle.collect(), timeout=30)
    return handle.trace


def _blob(trace):
    return repr([(n.inputs, n.outputs) for n in trace.nodes])


class TestBatchOps:
    @pytest.mark.asyncio
    async def test_an_excluded_input_is_absent(self):
        @op(exclude={"trace": ["api_key"]})
        def call(api_key: str):
            return {"ok": True}

        assert SECRET_IN not in _blob(await _trace_of(call, "t_in"))

    @pytest.mark.asyncio
    async def test_an_excluded_output_is_absent(self):
        @op(exclude={"trace": ["token"]})
        def call(api_key: str):
            return {"token": SECRET_OUT, "ok": True}

        assert SECRET_OUT not in _blob(await _trace_of(call, "t_out"))

    @pytest.mark.asyncio
    async def test_unexcluded_vars_still_appear(self):
        """Over-filtering would make the trace useless, which is a worse
        failure than the one being fixed here — it is silent."""

        @op(exclude={"trace": ["api_key"]})
        def call(api_key: str):
            return {"token": SECRET_OUT, "ok": True}

        blob = _blob(await _trace_of(call, "t_keep"))
        assert SECRET_OUT in blob
        assert "ok" in blob

    @pytest.mark.asyncio
    async def test_a_bare_op_is_unfiltered(self):
        @op
        def call(api_key: str):
            return {"token": SECRET_OUT}

        blob = _blob(await _trace_of(call, "t_bare"))
        assert SECRET_IN in blob and SECRET_OUT in blob

    @pytest.mark.asyncio
    async def test_the_list_form_covers_the_trace(self):
        """``exclude=["x"]`` means both channels, so the trace is one of
        them — this is the form the docstring shows first."""

        @op(exclude=["api_key"])
        def call(api_key: str):
            return {"ok": True}

        assert SECRET_IN not in _blob(await _trace_of(call, "t_list"))

    @pytest.mark.asyncio
    async def test_include_is_an_allowlist(self):
        @op(include=["ok"])
        def call(api_key: str):
            return {"token": SECRET_OUT, "ok": True}

        blob = _blob(await _trace_of(call, "t_allow"))
        assert SECRET_IN not in blob
        assert SECRET_OUT not in blob
        assert "ok" in blob

    @pytest.mark.asyncio
    async def test_a_checkpoint_only_exclusion_leaves_the_trace_alone(self):
        """The two channels are independent; filtering one must not filter
        the other."""

        @op(exclude={"checkpoint": ["api_key"]})
        def call(api_key: str):
            return {"ok": True}

        assert SECRET_IN in _blob(await _trace_of(call, "t_cp_only"))


class TestGeneratorOps:
    """Generators append one ``OpExecution`` per yield, on a separate code
    path from the batch record — both had to be filtered."""

    @pytest.mark.asyncio
    async def test_every_yield_is_filtered(self):
        @op(exclude={"trace": ["token"]})
        def gen(api_key: str):
            for i in range(3):
                yield {"token": SECRET_OUT, "i": i}

        blob = _blob(await _trace_of(gen, "t_gen"))
        assert SECRET_OUT not in blob
        assert "'i'" in blob, "the unfiltered var should survive every yield"

    @pytest.mark.asyncio
    async def test_a_generators_inputs_are_filtered(self):
        @op(exclude={"trace": ["api_key"]})
        def gen(api_key: str):
            yield {"i": 0}

        assert SECRET_IN not in _blob(await _trace_of(gen, "t_gen_in"))
