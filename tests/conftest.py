"""Shared fixtures: golden-update flag, a tmp ResourceHub, golden paths."""

from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden"


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite golden snapshots instead of comparing (review the diff after)",
    )


@pytest.fixture
def update_golden(request) -> bool:
    return bool(request.config.getoption("--update-golden"))


DOCS = GOLDEN / "docs"

RESOURCES = """\
kb_catalog:main:
  path: {root}/catalog.db
kb_blob:main:
  root: {root}/blobs
kb_lexical:main:
  path: {root}/lexical.db
vector_store:kb:
  api_type: faiss
  metric: cosine
  dim: 32
vector_store:kb2:
  api_type: faiss
  metric: cosine
  dim: 32
fake_embedding:hash:
  dim: 32
fake_reranking:overlap: {{}}
fake_llm:scripted:
  responses: []
"""


@pytest.fixture
def hub(tmp_path):
    """A fresh ResourceHub over a temporary catalog, blob store, in-memory FAISS index and HashEmbedder."""
    from operonx.core.registry import ResourceHub

    import operonx_kb  # noqa: F401 — registers kb_* categories
    import operonx_kb.testing.fakes  # noqa: F401 — registers fake_embedding

    path = tmp_path / "resources.yaml"
    path.write_text(RESOURCES.format(root=tmp_path / "kb"), encoding="utf-8")
    hub = ResourceHub.from_yaml(path)
    ResourceHub.set_instance(hub)
    yield hub
    ResourceHub.reset_instance()


@pytest.fixture
def kb(hub):
    """A KnowledgeBase with a 'docs' collection: structural chunker, HashEmbedder, FAISS index."""
    from operonx_kb import ChunkerSpec, CollectionSpec, DenseIndexSpec, KnowledgeBase
    from operonx_kb.testing import RecordingConsumer

    recorder = RecordingConsumer()
    kb = KnowledgeBase(trace=recorder)
    kb.recorder = recorder
    kb.create_collection(
        "docs",
        CollectionSpec(
            chunker=ChunkerSpec(max_tokens=120, min_tokens=16),
            dense=DenseIndexSpec(embedder="fake_embedding:hash", store="vector_store:kb"),
        ),
    )
    kb.embedder = hub.get("fake_embedding:hash")
    kb.store = hub.get("vector_store:kb")
    return kb


@pytest.fixture
def kbx(hub):
    """A KnowledgeBase with a 'docs' collection that has a dense and a lexical index and a
    declared filterable field. The embedder is reached as ``embedding:hash``, an alias of
    the fake, because operonx's EmbeddingOp (the query side) resolves ``embedding:`` keys only."""
    from operonx_kb import ChunkerSpec, CollectionSpec, DenseIndexSpec, KnowledgeBase
    from operonx_kb.model.collection import LexicalIndexSpec
    from operonx_kb.testing import RecordingConsumer

    hub.alias("embedding:hash", "fake_embedding:hash")
    recorder = RecordingConsumer()
    kb = KnowledgeBase(trace=recorder)
    kb.recorder = recorder
    kb.create_collection(
        "docs",
        CollectionSpec(
            chunker=ChunkerSpec(max_tokens=120, min_tokens=16),
            dense=DenseIndexSpec(embedder="hash", store="vector_store:kb"),
            lexical=LexicalIndexSpec(),
            filterable={"dept": "keyword"},
        ),
    )
    kb.embedder = hub.get("fake_embedding:hash")
    kb.store = hub.get("vector_store:kb")
    kb.lexical = hub.get("kb_lexical:main")
    return kb
