"""Tests for op-level caching.

Verifies:
- cache=True (in-memory only)
- cache="path" (in-memory + file persistence)
- Cache hit skips execution
- Different inputs get different cache entries
- Works with @op decorator syntax
- Works with EmbeddingOp/LLMOp-like patterns
"""

import json
import struct
import tempfile
from pathlib import Path

import pytest

from operonx.core import END, PARENT, START, GraphOp, Operon
from operonx.core.ops import op

pytestmark = pytest.mark.asyncio


# ============================================================================
# Helpers
# ============================================================================

call_count = 0


def reset_call_count():
    global call_count
    call_count = 0


# ============================================================================
# Basic cache tests
# ============================================================================


class TestOpCacheMemory:
    """Test cache=True (in-memory only)."""

    async def test_cache_hit_skips_execution(self):
        """Same inputs should return cached result without re-executing."""
        reset_call_count()

        @op(cache=True)
        def expensive(x: int):
            global call_count
            call_count += 1
            return {"result": x * 2}

        with GraphOp(name="cached") as g:
            step = expensive(x=PARENT["x"])
            START >> step >> END

        engine = Operon(g)

        # First call — cache miss
        r1 = await engine.run(inputs={"x": 5})
        assert r1["result"] == 10
        assert call_count == 1

        # Second call — same input → cache hit
        r2 = await engine.run(inputs={"x": 5})
        assert r2["result"] == 10
        assert call_count == 1  # NOT incremented

    async def test_different_inputs_no_collision(self):
        """Different inputs should produce different cache entries."""
        reset_call_count()

        @op(cache=True)
        def double(x: int):
            global call_count
            call_count += 1
            return {"result": x * 2}

        with GraphOp(name="cached2") as g:
            step = double(x=PARENT["x"])
            START >> step >> END

        engine = Operon(g)

        r1 = await engine.run(inputs={"x": 3})
        assert r1["result"] == 6
        assert call_count == 1

        r2 = await engine.run(inputs={"x": 7})
        assert r2["result"] == 14
        assert call_count == 2  # different input, cache miss

        # Repeat first input — cache hit
        r3 = await engine.run(inputs={"x": 3})
        assert r3["result"] == 6
        assert call_count == 2  # still 2


class TestOpCacheFile:
    """Test cache="path" (in-memory + file persistence)."""

    async def test_cache_saves_to_file(self, tmp_path):
        """Cache should save entries to binary file."""
        from operonx.core.ops._cache import FILE_MAGIC
        from operonx.core.ops.base import BaseOp

        cache_path = str(tmp_path / "test_cache.bin")

        @op(cache=cache_path)
        def compute(x: int):
            return {"result": x * x}

        with GraphOp(name="file_cached") as g:
            step = compute(x=PARENT["x"])
            START >> step >> END

        engine = Operon(g)
        await engine.run(inputs={"x": 4})
        await engine.run(inputs={"x": 5})

        # Save caches
        total = BaseOp.save_all_caches()
        assert total == 2

        # Verify file exists and has content
        data = Path(cache_path).read_bytes()
        assert data.startswith(FILE_MAGIC)
        (count,) = struct.unpack_from("<Q", data, len(FILE_MAGIC))
        assert count == 2

    async def test_cache_loads_from_file(self, tmp_path):
        """A saved cache answers a fresh process: the key is stable."""
        from operonx.core.ops.base import BaseOp

        cache_path = str(tmp_path / "preloaded.bin")
        calls = []

        @op(cache=cache_path)
        def slow(x: int):
            calls.append(x)
            return {"result": x + 1}

        def build():
            with GraphOp(name="preloaded") as g:
                step = slow(x=PARENT["x"])
                START >> step >> END
            return g

        assert (await Operon(build()).run(inputs={"x": 42}))["result"] == 43
        BaseOp.save_all_caches()
        BaseOp._cache_stores.clear()  # what a new process starts with

        r = await Operon(build()).run(inputs={"x": 42})
        assert r["result"] == 43
        assert calls == [42]  # the second run read the file

    async def test_old_format_file_starts_empty(self, tmp_path, caplog):
        """A file from the old key format is not misread as entries."""
        cache_path = tmp_path / "old.bin"
        entry = json.dumps({"result": 99}).encode()
        cache_path.write_bytes(struct.pack("<Q", 1) + struct.pack("<QI", 7, len(entry)) + entry)

        @op(cache=str(cache_path))
        def slow(x: int):
            return {"result": -1}

        with GraphOp(name="old") as g:
            step = slow(x=PARENT["x"])
            START >> step >> END

        r = await Operon(g).run(inputs={"x": 42})
        assert r["result"] == -1
        assert "older operonx" in caplog.text


class TestOpCacheDecorator:
    """Test @op(cache=...) decorator syntax."""

    async def test_op_decorator_cache_true(self):
        """@op(cache=True) should enable in-memory caching."""
        reset_call_count()

        @op(cache=True)
        def cached_op(text: str):
            global call_count
            call_count += 1
            return {"embedding": [0.1, 0.2, 0.3]}

        with GraphOp(name="decorator_cache") as g:
            step = cached_op(text=PARENT["text"])
            START >> step >> END

        engine = Operon(g)

        await engine.run(inputs={"text": "hello"})
        assert call_count == 1

        await engine.run(inputs={"text": "hello"})
        assert call_count == 1  # cache hit

        await engine.run(inputs={"text": "world"})
        assert call_count == 2  # cache miss


class TestOpCacheSerialize:
    """Test that cache config is serialized for Rust backend."""

    async def test_serialize_cache_true(self):
        @op(cache=True)
        def my_op(x: int):
            return {"result": x}

        with GraphOp(name="ser") as g:
            step = my_op(x=PARENT["x"])
            START >> step >> END

        engine = Operon(g)
        config = engine.graph.serialize()
        # Find the op config by looking for the one with cache
        op_configs = [v for v in config["ops"].values() if isinstance(v, dict) and v.get("cache")]
        assert len(op_configs) == 1
        assert op_configs[0]["cache"] is True

    async def test_serialize_cache_path(self):
        @op(cache="cache/my_op.bin")
        def my_op(x: int):
            return {"result": x}

        with GraphOp(name="ser2") as g:
            step = my_op(x=PARENT["x"])
            START >> step >> END

        engine = Operon(g)
        config = engine.graph.serialize()
        op_configs = [v for v in config["ops"].values() if isinstance(v, dict) and v.get("cache")]
        assert len(op_configs) == 1
        assert op_configs[0]["cache"] == "cache/my_op.bin"

    async def test_serialize_no_cache(self):
        @op
        def my_op(x: int):
            return {"result": x}

        with GraphOp(name="ser3") as g:
            step = my_op(x=PARENT["x"])
            START >> step >> END

        engine = Operon(g)
        config = engine.graph.serialize()
        op_configs = [v for v in config["ops"].values() if isinstance(v, dict)]
        for oc in op_configs:
            assert "cache" not in oc


class TestEmbeddingLikeCache:
    """Test cache with embedding-like ops (list inputs/outputs)."""

    async def test_embedding_cache(self):
        """Simulate an embedding op with cache — lists of floats."""
        reset_call_count()

        @op(cache=True)
        def embed(texts: list):
            global call_count
            call_count += 1
            return {"embeddings": [[0.1 * i for i in range(3)] for _ in texts]}

        with GraphOp(name="embed_cache") as g:
            step = embed(texts=PARENT["texts"])
            START >> step >> END

        engine = Operon(g)

        r1 = await engine.run(inputs={"texts": ["hello", "world"]})
        assert len(r1["embeddings"]) == 2
        assert call_count == 1

        r2 = await engine.run(inputs={"texts": ["hello", "world"]})
        assert len(r2["embeddings"]) == 2
        assert call_count == 1  # cache hit

        r3 = await engine.run(inputs={"texts": ["different"]})
        assert len(r3["embeddings"]) == 1
        assert call_count == 2  # cache miss


class TestLLMLikeCache:
    """Test cache with LLM-like ops (string input, dict output)."""

    async def test_llm_cache(self):
        """Simulate an LLM op with cache — deterministic prompts."""
        reset_call_count()

        @op(cache=True)
        def llm_call(prompt: str, model: str):
            global call_count
            call_count += 1
            return {"content": f"Response to: {prompt}", "model": model}

        with GraphOp(name="llm_cache") as g:
            step = llm_call(prompt=PARENT["prompt"], model=PARENT["model"])
            START >> step >> END

        engine = Operon(g)

        r1 = await engine.run(inputs={"prompt": "What is AI?", "model": "gpt-4"})
        assert "Response to" in r1["content"]
        assert call_count == 1

        # Same prompt + model → cache hit
        r2 = await engine.run(inputs={"prompt": "What is AI?", "model": "gpt-4"})
        assert r2["content"] == r1["content"]
        assert call_count == 1

        # Different model → cache miss
        r3 = await engine.run(inputs={"prompt": "What is AI?", "model": "gpt-3.5"})
        assert call_count == 2


# ============================================================================
# Cache key: what tells two cached calls apart
# ============================================================================


@op(cache=True)
def _a_impl(x: int) -> dict:
    return {"r": f"A{x}"}


@op(cache=True)
def _b_impl(x: int) -> dict:
    return {"r": f"B{x}"}


class TestCacheKey:
    """The key is the graph, the op's code and its inputs — not its name.

    Before 1.15 the store was keyed by ``op.full_name`` alone, and the full
    name is spelled from variable names: two different graphs built under
    the same engine variable, each with an op bound to the same name,
    answered each other's calls (evidence/probes/p2_interrupt_cache_ckpt.py,
    P6: ``gb`` returned ``A7``).
    """

    async def test_cache_isolated_across_graphs_with_same_names(self):
        from operonx import graph

        @graph
        def ga(x):
            c = _a_impl(x=x)
            START >> c >> END

        @graph
        def gb(x):
            c = _b_impl(x=x)
            START >> c >> END

        engine = Operon(ga, params={"x": None})
        r1 = (await engine.run(inputs={"x": 7}))["r"]
        engine = Operon(gb, params={"x": None})  # a different graph, same names
        r2 = (await engine.run(inputs={"x": 7}))["r"]

        assert (r1, r2) == ("A7", "B7")

    async def test_cache_key_distinguishes_objects_with_equal_str(self):
        """Two inputs that print the same are not the same input."""
        from dataclasses import dataclass

        @dataclass
        class Celsius:
            v: int

            def __str__(self):
                return "temp"

        @dataclass
        class Fahrenheit:
            v: int

            def __str__(self):
                return "temp"

        calls = []

        @op(cache=True)
        def read(t: object) -> dict:
            calls.append(t)
            return {"kind": type(t).__name__}

        with GraphOp(name="temps") as g:
            step = read(t=PARENT["t"])
            START >> step >> END

        engine = Operon(g)
        assert (await engine.run(inputs={"t": Celsius(1)}))["kind"] == "Celsius"
        assert (await engine.run(inputs={"t": Fahrenheit(1)}))["kind"] == "Fahrenheit"
        assert (await engine.run(inputs={"t": Celsius(1)}))["kind"] == "Celsius"
        assert len(calls) == 2  # the third call was a hit

    async def test_cache_key_changes_with_the_op_code(self):
        """Same graph name, op name, qualname and inputs; different body."""

        def build(plus_one: bool):
            if plus_one:

                @op(cache=True)
                def step(x: int) -> dict:
                    return {"r": x + 1}

            else:

                @op(cache=True)
                def step(x: int) -> dict:
                    return {"r": x + 2}

            with GraphOp(name="same") as g:
                s = step(x=PARENT["x"])
                START >> s >> END
            return g

        r1 = (await Operon(build(True)).run(inputs={"x": 1}))["r"]
        r2 = (await Operon(build(False)).run(inputs={"x": 1}))["r"]
        assert (r1, r2) == (2, 3)

    async def test_unencodable_input_fails_loudly(self):
        """An input with no exact encoding is an error, not a str() key."""

        class Opaque:
            def __str__(self):
                return "same"

        @op(cache=True)
        def read(t: object) -> dict:
            return {"ok": True}

        with GraphOp(name="opaque") as g:
            step = read(t=PARENT["t"])
            START >> step >> END

        out = await Operon(g).run(inputs={"t": Opaque()})
        assert "ok" not in out
        (err,) = out["$errors"].values()
        assert "cache" in err["message"] and "Opaque" in err["message"]

    async def test_store_is_bounded(self, monkeypatch):
        """The least recently used entry goes once a store is full."""
        from operonx.core.ops import _cache

        monkeypatch.setattr(_cache, "CACHE_MAX_ENTRIES", 2)
        seen = []

        @op(cache=True)
        def sq(x: int) -> dict:
            seen.append(x)
            return {"r": x * x}

        with GraphOp(name="bounded") as g:
            step = sq(x=PARENT["x"])
            START >> step >> END

        engine = Operon(g)
        for x in (1, 2, 1, 3, 1, 2):
            await engine.run(inputs={"x": x})
        # 1 miss, 2 miss, 1 hit (1 is now newest), 3 miss evicts 2,
        # 1 hit, 2 miss.
        assert seen == [1, 2, 3, 2]


class TestLLMOpCacheIdentity:
    """An LLM op's key covers what changes its answer besides its inputs."""

    def _scope(self, **kw):
        from operonx.core.ops._cache import op_scope
        from operonx.providers.ops import LLMOp

        with GraphOp(name="g") as g:
            llm = LLMOp(name="llm", **kw)
            START >> llm >> END
        g.build()
        return op_scope(g._ops["llm"])

    def test_model_fields_and_validators_are_in_the_key(self):
        base = self._scope(resource="m1")
        assert base == self._scope(resource="m1")
        assert base != self._scope(resource="m2")
        assert base != self._scope(resource="m1", fields=["x: int"])
        assert self._scope(resource="m1", fields=["x: int"], validators=lambda d: True) != (
            self._scope(resource="m1", fields=["x: int"], validators=lambda d: False)
        )
