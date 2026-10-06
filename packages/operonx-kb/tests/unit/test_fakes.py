import asyncio
import math

from operonx.core.registry import REGISTRY

from operonx_kb.testing.fakes import HashEmbedder, HashEmbeddingConfig


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def test_hash_embedder_is_deterministic_normalised_and_lexical():
    e = HashEmbedder(HashEmbeddingConfig(dim=64))
    out = asyncio.run(e.run(["annual leave days", "leave days per year", "parking fee"]))
    a, b, c = out["embeddings"]
    assert len(a) == 64 and math.isclose(_cos(a, a), 1.0, rel_tol=1e-9)
    assert _cos(a, b) > _cos(a, c)
    assert asyncio.run(e.run("annual leave days"))["embeddings"][0] == a
    assert e.calls == 2 and e.texts[-1] == "annual leave days"
    e.reset()
    assert e.calls == 0 and e.texts == []


def test_fake_embedding_category_is_registered():
    assert REGISTRY.get_class("fake_embedding") is HashEmbeddingConfig
