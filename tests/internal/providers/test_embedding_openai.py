"""OpenAI-protocol embeddings — the request that actually leaves the process.

No network: the SDK's HTTP client is swapped for an ``httpx.MockTransport``
that records each request and answers like the real API. What is pinned is
the wire — URL, auth header, body — because every way of getting it wrong
was quiet or far away until 1.11.2:

* the documented ``base_url: https://api.openai.com/v1`` posted to ``/v1``
  (a 404 from OpenAI) — only the undocumented ``…/v1/embeddings`` worked;
* ``dimensions`` was never sent, so a 256-wide config got 1536-wide vectors;
* Azure sent ``Authorization: Bearer <key>`` and no ``api-version``.
"""

import json
import warnings
from pathlib import Path

import httpx
import pytest
import yaml

from operonx.providers.embeddings import openai as openai_embeddings
from operonx.providers.embeddings.config import EmbeddingConfig, EmbeddingType
from operonx.providers.embeddings.factory import create_embedding
from operonx.providers.embeddings.openai import AzureOpenAIEmbedding, OpenAIEmbedding
from operonx.providers.embeddings.vllm import VLLMEmbedding

REPO = Path(__file__).resolve().parents[3]

# Mock-only: runs in the default `pytest`, unlike this directory's
# auto-marked integration tests (conftest.py).
pytestmark = pytest.mark.unit


@pytest.fixture
def wire(monkeypatch):
    """Every request the client sends, answered with vectors of ``width``."""
    seen = {"requests": [], "width": 4}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["requests"].append({"url": str(request.url), "headers": request.headers, "body": body})
        width = body.get("dimensions") or seen["width"]
        texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
        data = [
            {"object": "embedding", "index": i, "embedding": [float(i)] * width}
            for i in range(len(texts))
        ]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": body["model"],
                "data": data[::-1],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    monkeypatch.setattr(
        openai_embeddings,
        "create_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return seen


def _openai(**over):
    base = dict(
        api_type=EmbeddingType.OPENAI,
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model="text-embedding-3-small",
    )
    base.update(over)
    return EmbeddingConfig(**base)


def _azure(**over):
    base = dict(
        api_type=EmbeddingType.AZURE,
        api_key="az-key",
        base_url="https://acme.openai.azure.com",
        model="emb-deploy",
        api_version="2024-10-21",
    )
    base.update(over)
    return EmbeddingConfig(**base)


class TestDispatch:
    def test_openai_and_azure_get_the_sdk_client_vllm_keeps_the_exact_url_one(self):
        assert type(create_embedding(_openai())) is OpenAIEmbedding
        assert type(create_embedding(_azure())) is AzureOpenAIEmbedding
        vllm = EmbeddingConfig(
            api_type=EmbeddingType.VLLM, base_url="http://h/v1/embeddings", dimensions=8
        )
        assert type(create_embedding(vllm)) is VLLMEmbedding

    def test_an_unsupported_type_names_the_supported_ones(self):
        with pytest.raises(ValueError, match="supported"):
            create_embedding(EmbeddingConfig(api_type=EmbeddingType.GEMINI))


class TestOpenAI:
    async def test_the_documented_base_url_reaches_the_embeddings_endpoint(self, wire):
        out = await create_embedding(_openai()).run(["a", "b"])
        req = wire["requests"][-1]
        assert req["url"] == "https://api.openai.com/v1/embeddings"
        assert req["headers"]["authorization"] == "Bearer sk-test"
        assert req["body"]["model"] == "text-embedding-3-small" and req["body"]["input"] == [
            "a",
            "b",
        ]
        assert len(out["embeddings"]) == 2

    async def test_a_trailing_slash_changes_nothing(self, wire):
        await create_embedding(_openai(base_url="https://api.openai.com/v1/")).run("a")
        assert wire["requests"][-1]["url"] == "https://api.openai.com/v1/embeddings"

    async def test_the_old_full_endpoint_url_still_works_and_says_so(self, wire):
        with pytest.warns(DeprecationWarning, match="API root"):
            emb = create_embedding(_openai(base_url="https://api.openai.com/v1/embeddings"))
        await emb.run("a")
        assert wire["requests"][-1]["url"] == "https://api.openai.com/v1/embeddings"

    async def test_the_api_root_raises_no_warning(self, wire):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            create_embedding(_openai())

    async def test_dimensions_is_sent(self, wire):
        out = await create_embedding(_openai(dimensions=256)).run("a")
        assert wire["requests"][-1]["body"]["dimensions"] == 256
        assert len(out["embeddings"][0]) == 256

    async def test_no_dimensions_sends_none(self, wire):
        await create_embedding(_openai()).run("a")
        assert "dimensions" not in wire["requests"][-1]["body"]

    async def test_a_vector_of_another_width_raises(self, wire):
        """A server that ignores ``dimensions`` must not hand an index built
        for 256 a 4-wide vector."""
        emb = create_embedding(_openai(dimensions=256))
        original = emb.client.embeddings.create

        async def ignore_dimensions(model, input, **kwargs):  # noqa: A002
            kwargs.pop("dimensions", None)  # the server's default width: 4
            return await original(model=model, input=input, **kwargs)

        emb.client.embeddings.create = ignore_dimensions
        with pytest.raises(ValueError, match="width"):
            await emb.run("a")
        assert len(wire["requests"]) == 1  # it went out; the reply was refused

    async def test_vectors_come_back_in_input_order(self, wire):
        out = await create_embedding(_openai()).run(["x", "y", "z"])  # the mock answers reversed
        assert [v[0] for v in out["embeddings"]] == [0.0, 1.0, 2.0]

    def test_base_url_and_model_are_required(self):
        with pytest.raises(ValueError, match="base_url"):
            OpenAIEmbedding(_openai(base_url=None))
        with pytest.raises(ValueError, match="model"):
            OpenAIEmbedding(_openai(model=None))


class TestAzure:
    async def test_key_auth_uses_the_api_key_header_and_the_api_version(self, wire):
        await create_embedding(_azure()).run("a")
        req = wire["requests"][-1]
        url = httpx.URL(req["url"])
        assert url.path == "/openai/deployments/emb-deploy/embeddings"
        assert url.params["api-version"] == "2024-10-21"
        assert req["headers"]["api-key"] == "az-key"
        assert "authorization" not in req["headers"]

    def test_api_version_is_required(self):
        with pytest.raises(ValueError, match="api_version"):
            create_embedding(_azure(api_version=None))

    def test_a_deployment_url_is_refused_with_the_fix(self):
        with pytest.raises(ValueError, match="resource endpoint"):
            create_embedding(
                _azure(base_url="https://acme.openai.azure.com/openai/deployments/emb/embeddings")
            )


def _embedding_blocks():
    """Every ``api_type: openai`` embedding resource the repo tells people to write."""
    files = [REPO / "resources.yaml", *sorted((REPO / "examples").rglob("resources.yaml"))]
    for f in files:
        for key, block in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).items():
            if (
                key.startswith("embedding:")
                and isinstance(block, dict)
                and block.get("api_type") == "openai"
            ):
                yield pytest.param(block, id=f"{f.relative_to(REPO).as_posix()}:{key}")


@pytest.mark.parametrize("block", list(_embedding_blocks()))
async def test_every_shipped_openai_embedding_resource_reaches_the_endpoint(block, wire):
    """The examples and the root resources.yaml are what people copy — each
    must reach ``/embeddings`` as written, with no deprecation warning."""
    cfg = {**block, "api_key": "sk-test"}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        emb = create_embedding(EmbeddingConfig(**cfg))
    await emb.run("a")
    assert wire["requests"][-1]["url"].endswith("/v1/embeddings")
