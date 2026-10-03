"""The vector store contract, run against every shipped backend.

One suite, every backend: what ``search``, ``upsert`` and ``delete``
promise is checked the same way on each, so a backend cannot quietly
mean something else by "delete".

* ``faiss`` — an in-memory index; always runs.
* ``qdrant-local`` — qdrant-client's own local engine (``:memory:``),
  the same client code paths without a server; always runs.
* ``pgvector`` — a real Postgres with the ``vector`` extension, e.g. a
  throwaway container::

      docker run --rm -d -p 127.0.0.1:55439:5432 -e POSTGRES_PASSWORD=x pgvector/pgvector:pg16
      OPERONX_TEST_PG_DSN=postgresql://postgres:x@127.0.0.1:55439/postgres

* ``qdrant`` — a real Qdrant server, e.g.
  ``docker run --rm -d -p 127.0.0.1:16333:6333 qdrant/qdrant`` and
  ``OPERONX_TEST_QDRANT=http://127.0.0.1:16333``.

Unset, or set but unreachable, the live ones skip. Each test works in a
table or collection of its own and drops it.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional
from unittest.mock import patch

import pytest

from operonx.providers.vector_stores import (
    BaseVectorStore,
    VectorStoreConfig,
    VectorStoreMetric,
    VectorStoreType,
    create_vector_store,
)

pytestmark = pytest.mark.unit

PG_DSN = os.environ.get("OPERONX_TEST_PG_DSN", "")
QDRANT_URL = os.environ.get("OPERONX_TEST_QDRANT", "")

DIM = 4
E = [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]


@dataclass
class Backend:
    """A fresh, empty store plus the backend's own spelling of a filter."""

    name: str
    store: BaseVectorStore
    #: ``tenant == value`` in the backend's native dialect; None when the
    #: backend stores no metadata (FAISS).
    tenant_filter: Optional[Callable[[str], Dict[str, Any]]]
    #: Whether ``delete`` reports how many vectors it removed.
    counts_deletes: bool
    #: Drops what the test created.
    cleanup: Optional[Callable[[], Awaitable[None]]] = None


def _faiss() -> Backend:
    store = create_vector_store(
        VectorStoreConfig(api_type=VectorStoreType.FAISS, metric=VectorStoreMetric.IP, dim=DIM)
    )
    return Backend("faiss", store, None, counts_deletes=True)


def _qdrant_filter(value: str) -> Dict[str, Any]:
    return {"must": [{"key": "tenant", "match": {"value": value}}]}


async def _qdrant(url: Optional[str]) -> Backend:
    from qdrant_client import AsyncQdrantClient, models

    from operonx.providers.vector_stores.qdrant import QdrantVectorStore

    collection = f"t_{uuid.uuid4().hex[:10]}"
    client = AsyncQdrantClient(url=url) if url else AsyncQdrantClient(location=":memory:")
    await client.create_collection(
        collection_name=collection,
        vectors_config=models.VectorParams(size=DIM, distance=models.Distance.DOT),
    )
    config = VectorStoreConfig(
        api_type=VectorStoreType.QDRANT,
        url=url or "http://in-memory",
        collection=collection,
        metadata_columns=["tenant"],
    )
    # The process-wide client cache is keyed by URL; a client of the
    # test's own keeps one in-memory engine from leaking into the next.
    with patch("operonx.providers.vector_stores.qdrant._get_client", return_value=client):
        store = QdrantVectorStore(config)
    return Backend("qdrant", store, _qdrant_filter, counts_deletes=False)


async def _pgvector() -> Backend:
    import psycopg

    from operonx.providers.vector_stores._pg import close_pools

    table = f"t_{uuid.uuid4().hex[:10]}"
    async with await psycopg.AsyncConnection.connect(PG_DSN, autocommit=True) as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.execute(
            f"CREATE TABLE {table} (id bigint PRIMARY KEY, embedding vector({DIM}), tenant text)"
        )
    store = create_vector_store(
        VectorStoreConfig(
            api_type=VectorStoreType.PGVECTOR,
            metric=VectorStoreMetric.IP,
            dsn=PG_DSN,
            table=table,
            metadata_columns=["tenant"],
        )
    )

    async def drop():
        await close_pools()
        async with await psycopg.AsyncConnection.connect(PG_DSN, autocommit=True) as conn:
            await conn.execute(f"DROP TABLE IF EXISTS {table}")

    return Backend("pgvector", store, lambda v: {"tenant": v}, counts_deletes=True, cleanup=drop)


def _pg_reachable() -> bool:
    if not PG_DSN:
        return False
    try:
        import psycopg

        psycopg.connect(PG_DSN, connect_timeout=2).close()
    except Exception:  # noqa: BLE001 — unreachable is a skip, not a failure
        return False
    return True


def _qdrant_reachable() -> bool:
    if not QDRANT_URL:
        return False
    try:
        import httpx

        httpx.get(f"{QDRANT_URL.rstrip('/')}/collections", timeout=2).raise_for_status()
    except Exception:  # noqa: BLE001 — unreachable is a skip, not a failure
        return False
    return True


@pytest.fixture(params=["faiss", "qdrant-local", "pgvector", "qdrant"])
async def backend(request):
    name = request.param
    if name == "faiss":
        yield _faiss()
    elif name == "qdrant-local":
        b = await _qdrant(None)
        yield b
        await b.store._client.close()
    elif name == "pgvector":
        if not _pg_reachable():
            pytest.skip(
                "set OPERONX_TEST_PG_DSN to a reachable Postgres with pgvector "
                "(see this module's docstring)"
            )
        b = await _pgvector()
        yield b
        await b.cleanup()
    else:
        if not _qdrant_reachable():
            pytest.skip(
                "set OPERONX_TEST_QDRANT to a reachable Qdrant (see this module's docstring)"
            )
        b = await _qdrant(QDRANT_URL)
        yield b
        await b.store._client.delete_collection(b.store._default_collection)
        await b.store._client.close()


async def _seed(b: Backend) -> None:
    metadata = [{"tenant": t} for t in ("acme", "acme", "globex", "globex")]
    await b.store.upsert(ids=[1, 2, 3, 4], vectors=E, metadata=metadata)


async def _all_ids(b: Backend) -> set:
    ids, _, _ = await b.store.search(query_vector=[1, 1, 1, 1], top_k=10)
    return set(ids)


# -- search and upsert ------------------------------------------------------------------


async def test_upsert_then_search_finds_the_nearest_first(backend):
    await _seed(backend)
    ids, scores, metadata = await backend.store.search(query_vector=[0, 0.9, 0.1, 0], top_k=2)
    assert ids == [2, 3]
    assert scores[0] > scores[1]
    assert len(metadata) == 2


async def test_upsert_of_an_existing_id_replaces_it(backend):
    await _seed(backend)
    await backend.store.upsert(ids=[1], vectors=[[0, 0, 0, 1.0]], metadata=[{"tenant": "acme"}])
    ids, _, _ = await backend.store.search(query_vector=[0, 0, 0, 1.0], top_k=10)
    assert sorted(ids[:2]) == [1, 4]
    assert len(ids) == 4  # replaced, not duplicated


async def test_an_empty_index_returns_three_empty_lists(backend):
    assert await backend.store.search(query_vector=[1, 0, 0, 0]) == ([], [], [])


# -- delete -----------------------------------------------------------------------------


async def test_delete_by_ids_removes_exactly_those(backend):
    await _seed(backend)
    removed = await backend.store.delete(ids=[1, 3])
    assert await _all_ids(backend) == {2, 4}
    assert removed == (2 if backend.counts_deletes else None)


async def test_deleting_ids_that_are_not_there_is_not_an_error(backend):
    """GC retries a pass that died halfway; the second pass must succeed."""
    await _seed(backend)
    await backend.store.delete(ids=[1])
    removed = await backend.store.delete(ids=[1, 99])
    assert await _all_ids(backend) == {2, 3, 4}
    assert removed == (0 if backend.counts_deletes else None)


async def test_an_empty_id_list_deletes_nothing(backend):
    """``ids=[]`` is a batch that happened to be empty, not "no ids given"."""
    await _seed(backend)
    assert await backend.store.delete(ids=[]) == 0
    assert await _all_ids(backend) == {1, 2, 3, 4}


async def test_an_empty_upsert_batch_writes_nothing(backend):
    """An ingest pass with nothing new upserts an empty batch; that is a
    no-op, as ``delete(ids=[])`` is, not a length mismatch."""
    await _seed(backend)
    assert await backend.store.upsert([], []) is None
    assert await _all_ids(backend) == {1, 2, 3, 4}


async def test_delete_with_neither_ids_nor_filter_is_refused(backend):
    await _seed(backend)
    with pytest.raises(ValueError, match="ids= or filter="):
        await backend.store.delete()
    assert await _all_ids(backend) == {1, 2, 3, 4}


async def test_delete_with_both_ids_and_filter_is_refused(backend):
    await _seed(backend)
    with pytest.raises(ValueError, match="not both"):
        await backend.store.delete(ids=[1], filter={"tenant": "acme"})
    assert await _all_ids(backend) == {1, 2, 3, 4}


async def test_an_empty_filter_is_refused_rather_than_matching_everything(backend):
    await _seed(backend)
    with pytest.raises(ValueError, match="empty filter"):
        await backend.store.delete(filter={})
    assert await _all_ids(backend) == {1, 2, 3, 4}


async def test_delete_by_filter_removes_only_what_matches(backend):
    await _seed(backend)
    if backend.tenant_filter is None:
        with pytest.raises(ValueError, match="does not support metadata filtering"):
            await backend.store.delete(filter={"tenant": "acme"})
        assert await _all_ids(backend) == {1, 2, 3, 4}
        return
    removed = await backend.store.delete(filter=backend.tenant_filter("acme"))
    assert await _all_ids(backend) == {3, 4}
    assert removed == (2 if backend.counts_deletes else None)


async def test_a_deleted_id_can_be_written_again(backend):
    await _seed(backend)
    await backend.store.delete(ids=[2])
    await backend.store.upsert(ids=[2], vectors=[E[1]], metadata=[{"tenant": "acme"}])
    ids, _, _ = await backend.store.search(query_vector=E[1], top_k=1)
    assert ids == [2]


async def test_concurrent_deletes_of_disjoint_ids_all_land(backend):
    await _seed(backend)
    await asyncio.gather(backend.store.delete(ids=[1]), backend.store.delete(ids=[4]))
    assert await _all_ids(backend) == {2, 3}
