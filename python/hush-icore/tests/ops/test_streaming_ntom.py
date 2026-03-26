"""Tests for N-to-M streaming pattern.

Generator A yields N items → Generator B yields M items (M < N).
Output should only contain M items, not N.
"""

import asyncio

import pytest

from hush.core import END, PARENT, START, GraphOp, op
from hush.core.states import StateSchema


class TestNtoMStreaming:
    """N-to-M: upstream yields many, downstream yields few."""

    @pytest.mark.asyncio
    async def test_filter_pattern(self):
        """source yields 5 → filter yields 2 → consumer gets 2 results."""

        @op
        def source(n: int):
            for i in range(n):
                yield {"x": i}

        @op
        def filter_even(x: int):
            if x % 2 == 0:
                yield {"y": x}
            # odd: no yield (PENDING)

        @op
        def double(y: int):
            return {"result": y * 2}

        with GraphOp(name="ntom") as g:
            s = source(n=PARENT["n"])
            f = filter_even(x=s["x"])
            d = double(y=f["y"])
            START >> s >> f >> d >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 5})
        result = await g.run(state)

        # source yields 0,1,2,3,4 → filter yields 0,2,4 → double: 0,4,8
        assert result["result"] == [0, 4, 8]

    @pytest.mark.asyncio
    async def test_all_filtered(self):
        """source yields 5 → filter yields 0 → empty output."""

        @op
        def source(n: int):
            for i in range(n):
                yield {"x": i}

        @op
        def filter_none(x: int):
            # Never yield — all items filtered out
            if False:
                yield {"y": x}

        @op
        def consumer(y: int):
            return {"result": y}

        with GraphOp(name="ntom_empty") as g:
            s = source(n=PARENT["n"])
            f = filter_none(x=s["x"])
            c = consumer(y=f["y"])
            START >> s >> f >> c >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 5})
        result = await g.run(state)

        assert result["result"] == []

    @pytest.mark.asyncio
    async def test_one_to_many(self):
        """source yields 1 → expander yields 3 → consumer gets 3."""

        @op
        def source():
            yield {"x": 10}

        @op
        def expand(x: int):
            for i in range(3):
                yield {"y": x + i}

        @op
        def consumer(y: int):
            return {"result": y}

        with GraphOp(name="one_to_many") as g:
            s = source()
            e = expand(x=s["x"])
            c = consumer(y=e["y"])
            START >> s >> e >> c >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={})
        result = await g.run(state)

        assert result["result"] == [10, 11, 12]

    @pytest.mark.asyncio
    async def test_chained_generators_ntom(self):
        """3 chained generators: 10 → 5 → 2 items."""

        @op
        def source(n: int):
            for i in range(n):
                yield {"x": i}

        @op
        def half_filter(x: int):
            if x % 2 == 0:
                yield {"y": x}

        @op
        def quarter_filter(y: int):
            if y % 4 == 0:
                yield {"z": y}

        @op
        def consumer(z: int):
            return {"result": z * 10}

        with GraphOp(name="chain_ntom") as g:
            s = source(n=PARENT["n"])
            h = half_filter(x=s["x"])
            q = quarter_filter(y=h["y"])
            c = consumer(z=q["z"])
            START >> s >> h >> q >> c >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 10})
        result = await g.run(state)

        # 0,1,...,9 → 0,2,4,6,8 → 0,4,8 → 0,40,80
        assert result["result"] == [0, 40, 80]

    @pytest.mark.asyncio
    async def test_silence_only_no_output(self):
        """Simulates VAD pattern: many audio chunks, no speech detected → empty output."""

        @op
        def audio_source(n: int):
            for i in range(n):
                yield {"chunk": i, "is_speech": False}

        @op
        def vad(chunk: int, is_speech: bool):
            if is_speech:
                yield {"segment": chunk}

        @op
        def stt(segment: int):
            return {"text": f"transcript_{segment}"}

        with GraphOp(name="silence") as g:
            a = audio_source(n=PARENT["n"])
            v = vad(chunk=a["chunk"], is_speech=a["is_speech"])
            s = stt(segment=v["segment"])
            START >> a >> v >> s >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 50})
        result = await g.run(state)

        assert result["text"] == []

    @pytest.mark.asyncio
    async def test_speech_detected_once(self):
        """Simulates VAD: 50 chunks, speech at chunk 25 → 1 transcript."""

        @op
        def audio_source(n: int):
            for i in range(n):
                yield {"chunk": i, "is_speech": i == 25}

        @op
        def vad(chunk: int, is_speech: bool):
            if is_speech:
                yield {"segment": chunk}

        @op
        def stt(segment: int):
            return {"text": f"transcript_{segment}"}

        with GraphOp(name="one_speech") as g:
            a = audio_source(n=PARENT["n"])
            v = vad(chunk=a["chunk"], is_speech=a["is_speech"])
            s = stt(segment=v["segment"])
            START >> a >> v >> s >> END

        g.build()
        schema = StateSchema(g)
        state = schema.create_state(inputs={"n": 50})
        result = await g.run(state)

        assert result["text"] == ["transcript_25"]