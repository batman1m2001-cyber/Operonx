"""An op's first-ever call resolves its inputs like every later call (C1, S6).

`get_inputs` builds its index cache on the first call and reads through
it afterwards. The two used to be separate code: the first call unwrapped
`Media` and did not walk ancestor contexts, every later call did the
opposite. The cache is keyed by the schema, which every run of an engine
and every item of a fan-out share — so an op's body saw a different type,
or `None` instead of a value, depending only on whether it had run before.
"""

from operonx.core import END, PARENT, START, GraphOp, Operon, op
from operonx.core.media import Media
from operonx.core.states import MemoryState


@op
def make(n: int):
    for i in range(n):
        yield {"blob": Media(data=b"x" * (i + 1), mime_type="application/octet-stream")}


class TestMediaUnwrap:
    async def test_every_item_receives_raw_bytes(self):
        """C1, the `r6_media.py` shape: was ['bytes', 'Media', 'Media']."""
        seen = []

        @op
        def use(blob):
            seen.append(type(blob).__name__)
            return {"size": len(blob)}

        with GraphOp(name="media_g") as g:
            m = make(n=PARENT["n"])
            u = use(blob=m["blob"].parallel())
            START >> m >> u >> END

        await Operon(g).run(inputs={"n": 3})
        assert seen == ["bytes", "bytes", "bytes"]

    async def test_a_second_run_receives_raw_bytes_too(self):
        """The cache outlives the run: run 2 took the fast path from its
        first item, so it never unwrapped at all."""
        seen = []

        @op
        def produce():
            return {"audio": Media(data=b"wav", mime_type="audio/wav")}

        @op
        def consume(audio):
            seen.append(type(audio).__name__)
            return {"n": len(audio)}

        with GraphOp(name="twice") as g:
            p = produce()
            c = consume(audio=p["audio"])
            START >> p >> c >> END
        engine = Operon(g)

        await engine.run(inputs={})
        await engine.run(inputs={})
        assert seen == ["bytes", "bytes"]


class TestAncestorWalk:
    async def test_a_pushed_value_reaches_the_first_item_as_well(self):
        """S6: `seed` pushes `use.v` at ("main",); `use` runs per item at
        ("main", "[i]") and has no pull ref for `v`. The first item read
        the cell's default (None), every later item walked up to 10."""
        seen = []

        @op
        def seed():
            return {"v": 10}

        @op
        def gen(n: int):
            for i in range(n):
                yield {"i": i}

        @op
        def use(i, v=None):
            seen.append(v)
            return {"out": v}

        with GraphOp(name="s6") as g:
            s = seed()
            gn = gen(n=PARENT["n"])
            u = use(i=gn["i"])
            s["v"] >> u["v"]
            START >> s >> gn >> u >> END
        engine = Operon(g)

        idx = engine.schema.get_index("s6.u", "v")
        assert engine.schema._pull_refs[idx] is None  # the shape under test

        await engine.run(inputs={"n": 3})
        assert seen == [10, 10, 10]


class TestFirstCallEqualsLaterCalls:
    def test_every_resolution_rule_gives_the_same_answer_twice(self):
        """One state, one context, two calls: the first builds the cache,
        the second reads through it. Every rule `get_inputs` has must
        answer the same both times."""

        @op
        def src():
            return {"a": 1}

        @op
        def target(
            pulled=None,
            pushed=None,
            declared=None,
            literal=None,
            defaulted: str = "dflt",
            media=None,
            missing=None,
        ):
            return {"ok": True}

        with GraphOp(name="rules") as g:
            PARENT.declare(counter=5)
            s = src()
            t = target(pulled=s["a"], declared=PARENT["counter"], literal="lit")
            START >> s >> t >> END
        engine = Operon(g)
        schema = engine.schema

        def fresh():
            state = MemoryState(schema, inputs={})
            cells = state._cells
            # pulled: the producer wrote at the parent context only
            cells[schema.get_index("rules.s", "a")][("main",)] = 1
            # pushed: a value in the op's own cell at an ancestor, no pull ref
            cells[schema.get_index("rules.t", "pushed")][("main",)] = "up"
            # media: a Media in the op's own cell at the exact context
            cells[schema.get_index("rules.t", "media")][("main", "[0]")] = Media(
                data=b"m", mime_type="audio/wav"
            )
            return state

        op_ = g._ops["t"]
        ctx = ("main", "[0]")

        op_._input_cache = None
        state = fresh()
        first = op_.get_inputs(state, ctx)
        later = op_.get_inputs(state, ctx)
        on_a_fresh_state = op_.get_inputs(fresh(), ctx)

        expected = {
            "pulled": 1,
            "pushed": "up",
            "declared": 5,
            "literal": "lit",
            "defaulted": "dflt",
            "media": b"m",
        }
        assert first == expected
        assert later == expected
        assert on_a_fresh_state == expected
