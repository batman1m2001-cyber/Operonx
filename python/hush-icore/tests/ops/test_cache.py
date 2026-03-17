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

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops import op

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

        engine = Hush(g)

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

        engine = Hush(g)

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
        from hush.core.ops.base import BaseOp

        cache_path = str(tmp_path / "test_cache.bin")

        @op(cache=cache_path)
        def compute(x: int):
            return {"result": x * x}

        with GraphOp(name="file_cached") as g:
            step = compute(x=PARENT["x"])
            START >> step >> END

        engine = Hush(g)
        await engine.run(inputs={"x": 4})
        await engine.run(inputs={"x": 5})

        # Save caches
        total = BaseOp.save_all_caches()
        assert total == 2

        # Verify file exists and has content
        p = Path(cache_path)
        assert p.exists()
        data = p.read_bytes()
        assert len(data) > 8  # at least header

        # Parse header
        (count,) = struct.unpack_from("<Q", data, 0)
        assert count == 2

    async def test_cache_loads_from_file(self, tmp_path):
        """Cache should load entries from existing binary file."""
        from hush.core.ops.base import BaseOp

        cache_path = str(tmp_path / "preloaded.bin")

        # Pre-create a cache file with one entry
        entry = json.dumps({"result": 99}).encode()
        h = BaseOp._cache_hash({"x": 42})
        buf = struct.pack("<Q", 1)  # count=1
        buf += struct.pack("<QI", h, len(entry))
        buf += entry
        Path(cache_path).write_bytes(buf)

        @op(cache=cache_path)
        def slow(x: int):
            return {"result": -1}  # Should NOT be called if cache hits

        with GraphOp(name="preloaded") as g:
            step = slow(x=PARENT["x"])
            START >> step >> END

        engine = Hush(g)
        r = await engine.run(inputs={"x": 42})
        assert r["result"] == 99  # From cache, not from function


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

        engine = Hush(g)

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

        engine = Hush(g)
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

        engine = Hush(g)
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

        engine = Hush(g)
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

        engine = Hush(g)

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

        engine = Hush(g)

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
