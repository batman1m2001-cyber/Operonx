"""KBFilter compiled per backend: the tenant-leak conformance suite (track5 §12.3).

Every backend holds the same seeded entries — two collections, documents with
different tags, ACLs, MIME types, creation times and declared fields — all of
them equally close to the query, so only the filter decides what comes back.
For every filter of the vocabulary, alone and combined, a backend must return
**exactly** the entries :func:`~operonx_kb.model.filter.matches` accepts: never
one it rejects (a leak), never fewer (a lost document).

Backends: SQLite FTS5 always; pgvector and Postgres FTS with ``KB_TEST_PG_DSN``;
Qdrant with ``KB_TEST_QDRANT_URL``. FAISS stores no payload and compiles to a
post-filter; its end-to-end leak test is in ``tests/graphs/test_retrieve.py``.
"""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from operonx_kb.errors import FilterError
from operonx_kb.model.collection import CollectionSpec
from operonx_kb.model.filter import KBFilter, index_payload, matches
from operonx_kb.retrieval.filters import PostFilter, native_filter

PG = os.environ.get("KB_TEST_PG_DSN")
QDRANT = os.environ.get("KB_TEST_QDRANT_URL")
SPEC = CollectionSpec(
    filterable={
        "dept": "keyword",
        "year": "int",
        "score": "float",
        "active": "bool",
        "due": "datetime",
        "labels": "keyword[]",
    }
)
T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
DEPTS = ["hr", "it", "finance"]
TAGS = [["policy"], ["policy", "vn"], ["faq"], [], ["vn", "faq", "policy"]]
ACLS = [["team:hr"], ["team:it", "user:7"], [], ["user:7"], ["team:hr", "user:9"], ["team:it"], []]
MIMES = ["text/markdown", "application/pdf", "text/html"]
LABELS = [["a"], ["a", "b"], ["c"], [], ["b"]]


def _entries():
    """60 entries; each attribute cycles with its own period, so they vary independently."""
    out = []
    for i in range(60):
        collection = "tenant_a" if i % 2 else "tenant_b"
        metadata = {
            "dept": DEPTS[i % 3],
            "year": 2024 + (i // 2) % 3,
            "score": [0.5, 1.5, 2.25][(i // 3) % 3],
            "active": bool((i // 5) % 2),
            "due": (T0 + timedelta(days=(i // 2) % 4)).isoformat(),
            "labels": LABELS[i % 5],
        }
        if i % 7 == 0:
            metadata = {}  # a document without the declared fields
        payload = index_payload(
            spec=SPEC, collection_id=collection, document_id=f"doc_{i % 10}",
            tags=TAGS[(i // 2) % 5], acl=ACLS[i % 7], mime=MIMES[(i // 4) % 3],
            created_at=T0 + timedelta(hours=i), metadata=metadata,
        )  # fmt: skip
        out.append((1000 + i, payload))
    return out


ENTRIES = _entries()

FILTERS = {
    "collection only": KBFilter(),
    "document_ids": KBFilter(document_ids=["doc_1", "doc_4", "doc_9"]),
    "tags_any": KBFilter(tags_any=["vn"]),
    "tags_any two": KBFilter(tags_any=["faq", "vn"]),
    "tags_all": KBFilter(tags_all=["policy", "vn"]),
    "acl_any": KBFilter(acl_any=["user:7"]),
    "acl_any two": KBFilter(acl_any=["team:hr", "team:it"]),
    "mime_in": KBFilter(mime_in=["application/pdf"]),
    "created_after": KBFilter(created_after=T0 + timedelta(hours=30)),
    "created_before": KBFilter(created_before=T0 + timedelta(hours=30)),
    "created window": KBFilter(
        created_after=T0 + timedelta(hours=11), created_before=T0 + timedelta(hours=40)
    ),
    "keyword": KBFilter(fields={"dept": "it"}),
    "keyword any": KBFilter(fields={"dept": ["it", "finance"]}),
    "int": KBFilter(fields={"year": 2025}),
    "float": KBFilter(fields={"score": 2.25}),
    "bool": KBFilter(fields={"active": True}),
    "datetime": KBFilter(fields={"due": (T0 + timedelta(days=2)).isoformat()}),
    "keyword[] contains": KBFilter(fields={"labels": "b"}),
    "keyword[] any": KBFilter(fields={"labels": ["b", "c"]}),
    "combined": KBFilter(
        tags_any=["policy"], acl_any=["user:7", "team:hr"], fields={"active": False}
    ),
}


def expected(flt, collection):
    checked = flt.checked(SPEC)
    return {key for key, p in ENTRIES if matches(checked, collection, p)}


# ── backends: each seeds ENTRIES and answers search(native filter) -> ids ────────


def _sqlite_fts(tmp_path):
    from operonx_kb.stores.lexical.sqlite import SqliteLexicalIndex

    idx = SqliteLexicalIndex(tmp_path / "lex.db")
    idx.upsert([k for k, _ in ENTRIES], [["same"]] * len(ENTRIES), [p for _, p in ENTRIES])
    return idx, lambda native: set(idx.search(["same"], top_k=1000, filter=native)[0]), None


def _postgres_fts(tmp_path):
    from operonx_kb.stores.lexical.postgres import PostgresLexicalIndex

    idx = PostgresLexicalIndex(PG, schema=f"kbflt_{uuid.uuid4().hex[:10]}")
    idx.upsert([k for k, _ in ENTRIES], [["same"]] * len(ENTRIES), [p for _, p in ENTRIES])

    def close():
        idx.drop_schema()
        idx.close()

    return idx, lambda native: set(idx.search(["same"], top_k=1000, filter=native)[0]), close


PG_COLUMNS = (
    "kb_collection text, kb_document text, kb_tags text[], kb_acl text[], kb_mime text, "
    "kb_created double precision, kb_f_dept text, kb_f_year bigint, kb_f_score double precision, "
    "kb_f_active boolean, kb_f_due double precision, kb_f_labels text[]"
)
META = [c.split()[0] for c in PG_COLUMNS.split(", ")]


def _pgvector(tmp_path):
    import psycopg
    from operonx.providers.vector_stores.config import VectorStoreConfig
    from operonx.providers.vector_stores.pgvector import PgVectorStore

    table = f"kbflt_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(PG, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(
            f"CREATE TABLE {table} (id bigint PRIMARY KEY, embedding vector(4), {PG_COLUMNS})"
        )
    store = PgVectorStore(
        VectorStoreConfig(api_type="pgvector", dsn=PG, table=table, metric="cosine", metadata_columns=META)
    )  # fmt: skip

    # One loop for the store's life: its async pool belongs to the loop that opened it.
    loop = asyncio.new_event_loop()
    loop.run_until_complete(
        store.upsert(
            [k for k, _ in ENTRIES], [[1, 0, 0, 0]] * len(ENTRIES), [p for _, p in ENTRIES]
        )
    )

    def search(native):
        return set(
            loop.run_until_complete(store.search([1, 0, 0, 0], top_k=1000, filter=native))[0]
        )

    def close():
        from operonx.providers.vector_stores._pg import close_pools

        loop.run_until_complete(close_pools())
        loop.close()
        with psycopg.connect(PG, autocommit=True) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {table}")

    return store, search, close


def _qdrant(tmp_path):
    from operonx.providers.vector_stores.config import VectorStoreConfig
    from operonx.providers.vector_stores.qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient, models

    name = f"kbflt_{uuid.uuid4().hex[:10]}"
    client = QdrantClient(url=QDRANT)
    client.create_collection(
        name, vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE)
    )
    store = QdrantVectorStore(
        VectorStoreConfig(api_type="qdrant", url=QDRANT, collection=name, metric="cosine")
    )
    asyncio.run(
        store.upsert(
            [k for k, _ in ENTRIES], [[1, 0, 0, 0]] * len(ENTRIES), [p for _, p in ENTRIES]
        )
    )

    def search(native):
        return set(asyncio.run(store.search([1, 0, 0, 0], top_k=1000, filter=native))[0])

    def close():
        client.delete_collection(name)
        client.close()

    return store, search, close


BACKENDS = [
    pytest.param(_sqlite_fts, id="sqlite_fts"),
    pytest.param(
        _postgres_fts,
        id="postgres_fts",
        marks=pytest.mark.skipif(not PG, reason="set KB_TEST_PG_DSN"),
    ),
    pytest.param(
        _pgvector, id="pgvector", marks=pytest.mark.skipif(not PG, reason="set KB_TEST_PG_DSN")
    ),
    pytest.param(
        _qdrant, id="qdrant", marks=pytest.mark.skipif(not QDRANT, reason="set KB_TEST_QDRANT_URL")
    ),
]


@pytest.fixture(params=BACKENDS)
def backend(request, tmp_path):
    obj, search, close = request.param(tmp_path)
    yield obj, search
    if close:
        close()


def test_the_seed_exercises_every_condition():
    """Each filter keeps some entries and drops others in both collections (else it tests nothing)."""
    for name, flt in FILTERS.items():
        for collection in ("tenant_a", "tenant_b"):
            got = expected(flt, collection)
            assert 0 < len(got) < len(ENTRIES), (name, collection)


@pytest.mark.parametrize("name", list(FILTERS))
@pytest.mark.parametrize("collection", ["tenant_a", "tenant_b"])
def test_backend_returns_exactly_what_the_reference_accepts(backend, name, collection):
    obj, search = backend
    native = native_filter(obj, FILTERS[name].checked(SPEC), collection)
    got = search(native)
    want = expected(FILTERS[name], collection)
    leaked = got - want
    assert not leaked, f"{name}: returned entries the filter rejects: {sorted(leaked)}"
    assert got == want, f"{name}: lost {sorted(want - got)}"


def test_another_tenants_entries_never_come_back(backend):
    obj, search = backend
    a = search(native_filter(obj, KBFilter().checked(SPEC), "tenant_a"))
    b = search(native_filter(obj, KBFilter().checked(SPEC), "tenant_b"))
    assert a and b and not a & b
    assert search(native_filter(obj, KBFilter().checked(SPEC), "tenant_c")) == set()


def test_an_undeclared_field_raises_before_any_backend_sees_it():
    with pytest.raises(FilterError, match="not filterable"):
        KBFilter(fields={"tenant": "acme"}).checked(SPEC)


def test_faiss_compiles_to_a_post_filter(tmp_path):
    from operonx.providers.vector_stores.config import VectorStoreConfig
    from operonx.providers.vector_stores.faiss import FaissVectorStore

    store = FaissVectorStore(VectorStoreConfig(api_type="faiss", dim=4, metric="cosine"))
    assert isinstance(native_filter(store, KBFilter().checked(SPEC), "tenant_a"), PostFilter)


def test_a_backend_without_a_compiler_is_refused():
    with pytest.raises(FilterError, match="no KBFilter compiler"):
        native_filter(object(), KBFilter().checked(SPEC), "tenant_a")
