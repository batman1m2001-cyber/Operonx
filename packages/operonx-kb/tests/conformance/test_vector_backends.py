"""The KB's index contract on every operonx vector store it supports.

FAISS always; pgvector when ``KB_TEST_PG_DSN`` names a Postgres with the
``vector`` extension available (the catalog then lives in the same database,
the one-database deployment of track5 §12.2); Qdrant when ``KB_TEST_QDRANT_URL``
names a server. Each test creates its own table or collection and drops it.

Per backend: ingest, re-ingest (no writes), a one-paragraph edit (one vector
in, one out), purge (its vectors gone), GC of a crashed delete, and a rebuild
into a second table/collection that reproduces the id set.
"""

import asyncio
import os
import uuid
from pathlib import Path

import pytest

from operonx_kb.model.collection import CollectionSpec
from operonx_kb.model.ids import document_id, vector_id
from operonx_kb.retrieval.filters import pgvector_columns

DOCS = Path(__file__).parents[1] / "golden" / "docs"
CORPUS = [
    DOCS / n
    for n in (
        "engineering_guide.md",
        "careers.html",
        "handbook.docx",
        "budget.xlsx",
        "quy_trinh_vi.html",
    )
]
PG = os.environ.get("KB_TEST_PG_DSN")
QDRANT = os.environ.get("KB_TEST_QDRANT_URL")
DIM = 32

BACKENDS = [
    "faiss",
    pytest.param("pgvector", marks=pytest.mark.skipif(not PG, reason="set KB_TEST_PG_DSN")),
    pytest.param("qdrant", marks=pytest.mark.skipif(not QDRANT, reason="set KB_TEST_QDRANT_URL")),
]


def run(coro):
    return asyncio.run(coro)


PAYLOAD = pgvector_columns(CollectionSpec())


def _pg_tables(names, drop=False):
    import psycopg

    columns = ", ".join(f"{c} {t}" for c, t in PAYLOAD.items())
    with psycopg.connect(PG, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        for name in names:
            conn.execute(f"DROP TABLE IF EXISTS {name}")
            if not drop:
                conn.execute(
                    f"CREATE TABLE {name} (id bigint PRIMARY KEY, embedding vector({DIM}), {columns})"
                )


def _qdrant_collections(names, drop=False):
    from qdrant_client import QdrantClient, models

    client = QdrantClient(url=QDRANT)
    for name in names:
        if client.collection_exists(name):
            client.delete_collection(name)
        if not drop:
            client.create_collection(
                name, vectors_config=models.VectorParams(size=DIM, distance=models.Distance.COSINE)
            )
    client.close()


@pytest.fixture(params=BACKENDS)
def kb(request, tmp_path):
    from operonx.core.registry import ResourceHub

    import operonx_kb  # noqa: F401
    import operonx_kb.testing.fakes  # noqa: F401
    from operonx_kb import ChunkerSpec, CollectionSpec, DenseIndexSpec, KnowledgeBase

    tag = uuid.uuid4().hex[:10]
    names = [f"kbvec_{tag}_a", f"kbvec_{tag}_b"]
    backend = request.param
    catalog = f"kb_catalog:main:\n  path: {tmp_path}/c.db\n"
    if backend == "faiss":
        store = "  api_type: faiss\n  metric: cosine\n  dim: 32\n  collections: {}\n"
        stores = {
            "kb": store.replace("  collections: {}\n", ""),
            "kb2": store.replace("  collections: {}\n", ""),
        }
        names = [None, None]
    elif backend == "pgvector":
        _pg_tables(names)
        catalog = f"kb_catalog:main:\n  api_type: postgres\n  dsn: {PG}\n  db_schema: kb_{tag}\n"
        stores = {
            k: f"  api_type: pgvector\n  metric: cosine\n  dsn: {PG}\n  table: {n}\n"
            f"  metadata_columns: [{', '.join(PAYLOAD)}]\n"
            for k, n in zip(("kb", "kb2"), names)
        }
    else:
        _qdrant_collections(names)
        stores = {
            k: f"  api_type: qdrant\n  metric: cosine\n  url: {QDRANT}\n  collection: {n}\n"
            for k, n in zip(("kb", "kb2"), names)
        }
    yaml = catalog + f"kb_blob:main:\n  root: {tmp_path}/b\nfake_embedding:hash:\n  dim: {DIM}\n"
    yaml += "".join(f"vector_store:{k}:\n{v}" for k, v in stores.items())
    (tmp_path / "r.yaml").write_text(yaml, encoding="utf-8")
    hub = ResourceHub.from_yaml(tmp_path / "r.yaml")
    ResourceHub.set_instance(hub)
    kb = KnowledgeBase()
    kb.create_collection(
        "docs",
        CollectionSpec(
            chunker=ChunkerSpec(max_tokens=120, min_tokens=16),
            dense=DenseIndexSpec(embedder="fake_embedding:hash", store="vector_store:kb"),
        ),
    )
    kb.hub, kb.backend = hub, backend
    yield kb
    if backend == "pgvector":
        kb.catalog.drop_schema()
        kb.catalog.close()
        _pg_tables(names, drop=True)
    elif backend == "qdrant":
        _qdrant_collections(names, drop=True)
    ResourceHub.reset_instance()


def held(kb, key="vector_store:kb"):
    """Every id a store holds (a nearest-neighbour search with a large k returns all)."""
    store = kb.hub.get(key)
    ids, _, _ = run(store.search([1.0] + [0.0] * (DIM - 1), top_k=10_000))
    return {int(i) for i in ids}


def active(kb):
    return {vector_id(c) for c in kb.catalog.active_chunk_ids(collection_id="docs")}


def _manual(n_sections, edited=-1):
    parts = ["# Manual\n"]
    for i in range(n_sections):
        text = f"Section {i} explains rule number {i} in enough words to be its own chunk."
        parts.append(
            f"## Topic {i}\n\n{text.replace('rule number', 'the revised rule') if i == edited else text}\n"
        )
    return "\n".join(parts)


def test_ingest_reingest_edit_purge_gc(kb, tmp_path):
    for p in CORPUS:
        assert run(kb.add("docs", str(p)))["action"] == "new"
    assert held(kb) == active(kb) and kb.verify("docs").ok
    assert [run(kb.add("docs", str(p)))["action"] for p in CORPUS] == ["skip"] * len(CORPUS)

    manual = tmp_path / "manual.md"
    manual.write_text(_manual(20))
    run(kb.add("docs", str(manual)))
    manual.write_text(_manual(20, edited=7))
    edit = run(kb.add("docs", str(manual)))
    assert edit["stats"]["chunking"]["new"] == 1 and edit["stats"]["chunking"]["removed"] == 1
    assert held(kb) == active(kb)

    target = str(DOCS / "handbook.docx")
    gone = set(
        kb.catalog.index_entries(
            "vector_store:kb", "", document_id=document_id("docs", target)
        ).values()
    )
    report = run(kb.delete("docs", target, purge=True))
    assert report["purged"] and held(kb) & gone == set() and held(kb) == active(kb)

    kb.catalog.tombstone(
        document_id("docs", str(manual))
    )  # a delete that crashed before its index step
    gc = run(kb.gc("docs"))
    assert gc["stale"] > 0 and held(kb) == active(kb) and kb.verify("docs").ok


def test_rebuild_into_a_second_table_or_collection(kb):
    for p in CORPUS:
        run(kb.add("docs", str(p)))
    expected = held(kb)
    calls = kb.hub.get("fake_embedding:hash").calls
    report = run(kb.rebuild("docs", store="vector_store:kb2", drop_previous=True))
    assert report["upserted"] == len(expected) and kb.hub.get("fake_embedding:hash").calls == calls
    assert held(kb, "vector_store:kb2") == expected and held(kb) == set()
    assert kb.verify("docs").ok
