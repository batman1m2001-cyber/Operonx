"""``resource=`` as a graph input (MODULE_LEVEL_GRAPHS_PLAN M1): one module-level graph, the
model or store chosen per run — concurrent runs with different keys stay apart."""

from __future__ import annotations

import asyncio

import pytest

from operonx import END, START, Operon, graph
from operonx.core.registry import ResourceHub
from operonx.providers.ops import EmbeddingOp, LLMOp

pytestmark = pytest.mark.unit

RESOURCES = """
llm:alpha:
  api_type: fake
  script: [{delay: 0.05, text: "from alpha"}]
llm:beta:
  api_type: fake
  script: ["from beta"]
"""


@pytest.fixture
def hub(tmp_path):
    path = tmp_path / "resources.yaml"
    path.write_text(RESOURCES)
    saved = ResourceHub._instance
    ResourceHub.set_instance(ResourceHub.from_yaml(path))
    try:
        yield
    finally:
        ResourceHub._instance = saved


@graph
def chat(model, question):
    a = LLMOp.of(resource=model, messages=[{"role": "user", "content": "hi"}])
    START >> a >> END


def test_one_graph_answers_with_the_model_each_run_names(hub):
    engine = Operon(chat, params={"model": None, "question": None})

    async def both():
        return await asyncio.gather(  # alpha is slower: the runs overlap
            engine.run({"model": "alpha", "question": "q"}),
            engine.run({"model": "beta", "question": "q"}),
        )

    a, b = asyncio.run(both())
    assert (a["content"], a["model_used"]) == ("from alpha", "alpha")
    assert (b["content"], b["model_used"]) == ("from beta", "beta")


def test_a_run_without_the_resource_fails_naming_it(hub):
    out = asyncio.run(Operon(chat, params={"model": None, "question": None}).run({"question": "q"}))
    assert "$errors" in out


def test_ratios_or_batch_need_a_fixed_resource():
    with pytest.raises(ValueError, match="one key per call"):

        @graph
        def bad(model):
            a = LLMOp.of(resource=model, batch_mode=True, prompt="x")
            START >> a >> END

        Operon(bad, params={"model": None})


@graph
def fixed():
    a = LLMOp.of(resource="beta", messages=[{"role": "user", "content": "hi"}])
    START >> a >> END


def test_a_fixed_resource_works_as_before(hub):
    out = asyncio.run(Operon(fixed).run({}))
    assert (out["content"], out["model_used"]) == ("from beta", "beta")


class _Embedder:
    def __init__(self, n):
        self.n = n

    async def run(self, texts):
        return {"embeddings": [[float(self.n)] * 2 for _ in texts]}


class _Hub:
    def get(self, key):
        return {"embedding:one": _Embedder(1), "embedding:two": _Embedder(2)}[key]


@graph
def embed(embedder, texts):
    e = EmbeddingOp.of(resource=embedder, texts=texts)
    START >> e >> END


def test_embedding_resolves_its_resource_per_call(monkeypatch):
    import operonx.providers.ops.embedding as mod

    monkeypatch.setattr(mod, "resolve_hub", lambda: _Hub())
    engine = Operon(embed, params={"embedder": None, "texts": None})
    one = asyncio.run(engine.run({"embedder": "one", "texts": ["x"]}))
    two = asyncio.run(engine.run({"embedder": "two", "texts": ["x"]}))
    assert one["embeddings"] == [[1.0, 1.0]] and two["embeddings"] == [[2.0, 2.0]]
