"""Tests for the doc-store layer and DocFetchOp.

Fully mocked — no Postgres required, so these run on every PR (see the
``unit`` marker note in test_vector_search.py).
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from operonx.providers.doc_stores import (
    BaseDocStore,
    DocStoreConfig,
    DocStoreType,
    create_doc_store,
    partition_by_ids,
    reorder_by_ids,
)

pytestmark = pytest.mark.unit


# =============================================================================
# Order restoration — the silent-misalignment footgun
# =============================================================================


class TestReorderByIds:
    def test_restores_requested_order(self):
        # A real store returns rows in whatever order it likes.
        rows = [{"id": 3, "t": "c"}, {"id": 1, "t": "a"}, {"id": 2, "t": "b"}]
        out = reorder_by_ids(rows, [1, 2, 3])
        assert [r["id"] for r in out] == [1, 2, 3]

    def test_score_order_is_preserved_not_sorted(self):
        # Vector search returns ids by score, which is NOT ascending id.
        rows = [{"id": 1}, {"id": 2}, {"id": 3}]
        out = reorder_by_ids(rows, [3, 1, 2])
        assert [r["id"] for r in out] == [3, 1, 2]

    def test_missing_ids_are_dropped_from_rows(self):
        out = reorder_by_ids([{"id": 1}], [1, 99])
        assert [r["id"] for r in out] == [1]

    def test_custom_key(self):
        rows = [{"_key": "b"}, {"_key": "a"}]
        out = reorder_by_ids(rows, ["a", "b"], key="_key")
        assert [r["_key"] for r in out] == ["a", "b"]

    def test_rows_without_the_key_are_ignored(self):
        out = reorder_by_ids([{"nope": 1}, {"id": 5}], [5])
        assert out == [{"id": 5}]

    def test_empty_inputs(self):
        assert reorder_by_ids([], []) == []
        assert reorder_by_ids([{"id": 1}], []) == []


class TestPartitionByIds:
    def test_reports_missing(self):
        ordered, missing = partition_by_ids([{"id": 1}], [1, 2, 3])
        assert [r["id"] for r in ordered] == [1]
        assert missing == [2, 3]

    def test_no_missing(self):
        ordered, missing = partition_by_ids([{"id": 1}, {"id": 2}], [1, 2])
        assert len(ordered) == 2
        assert missing == []

    def test_duplicate_requested_ids_yield_the_row_twice(self):
        ordered, missing = partition_by_ids([{"id": 1}], [1, 1])
        assert len(ordered) == 2
        assert missing == []

    def test_duplicate_rows_keep_first(self):
        ordered, _ = partition_by_ids([{"id": 1, "v": "first"}, {"id": 1, "v": "second"}], [1])
        assert ordered[0]["v"] == "first"

    def test_type_mismatch_counts_as_missing(self):
        # int id from the index vs str key in the store — a real drift bug,
        # and one that should surface rather than silently return nothing.
        ordered, missing = partition_by_ids([{"id": "1"}], [1])
        assert ordered == []
        assert missing == [1]


# =============================================================================
# Config + factory
# =============================================================================


class TestConfigAndFactory:
    def test_category_is_doc_store(self):
        assert DocStoreConfig._category == "doc_store"

    def test_backend_types(self):
        assert {t.value for t in DocStoreType} == {"postgres", "mongo", "redis", "memory"}

    def test_default_id_field(self):
        assert DocStoreConfig().id_field == "id"

    def test_unsupported_type_raises(self):
        cfg = DocStoreConfig(dsn="postgresql://x")
        cfg.api_type = "nope"
        with pytest.raises(ValueError, match="Unsupported doc store"):
            create_doc_store(cfg)

    def test_postgres_requires_dsn(self):
        with pytest.raises(ValueError, match="requires dsn="):
            create_doc_store(DocStoreConfig(api_type=DocStoreType.POSTGRES))

    def test_missing_extra_message(self):
        from operonx.providers.doc_stores.factory import _missing_extra_message

        msg = _missing_extra_message("MongoDocStore", "mongo", ImportError("no motor"))
        assert "pip install operonx[mongo]" in msg


# =============================================================================
# BaseDocStore — ordering guaranteed in the base, not per backend
# =============================================================================


class _StubStore(BaseDocStore):
    """Returns rows deliberately out of order, like a real database."""

    __slots__ = ("rows", "calls", "config", "bound")

    def __init__(self, rows, config=None, bound="io"):
        self.rows = rows
        self.calls = []
        self.config = config if config is not None else DocStoreConfig(dsn="x")
        self.bound = bound

    async def _fetch(self, ids, collection=None, fields=None, id_field="id"):
        self.calls.append(
            {"ids": list(ids), "collection": collection, "fields": fields, "id_field": id_field}
        )
        return list(reversed([r for r in self.rows if r["id"] in set(ids)]))


class TestBaseDocStoreOrdering:
    @pytest.mark.asyncio
    async def test_base_restores_order_for_any_backend(self):
        # The whole reason ordering lives in the base: a backend that
        # returns arbitrary order still can't produce misaligned output.
        store = _StubStore([{"id": 1}, {"id": 2}, {"id": 3}])
        rows, missing = await store.fetch([3, 1, 2])
        assert [r["id"] for r in rows] == [3, 1, 2]
        assert missing == []

    @pytest.mark.asyncio
    async def test_base_reports_missing(self):
        store = _StubStore([{"id": 1}])
        rows, missing = await store.fetch([1, 42])
        assert [r["id"] for r in rows] == [1]
        assert missing == [42]

    @pytest.mark.asyncio
    async def test_empty_ids_short_circuits_without_hitting_backend(self):
        store = _StubStore([{"id": 1}])
        rows, missing = await store.fetch([])
        assert rows == [] and missing == []
        assert store.calls == []

    @pytest.mark.asyncio
    async def test_passes_through_collection_fields_id_field(self):
        store = _StubStore([{"id": 1}])
        await store.fetch([1], collection="docs", fields=["id", "title"], id_field="id")
        call = store.calls[0]
        assert call["collection"] == "docs"
        assert call["fields"] == ["id", "title"]
        assert call["id_field"] == "id"


# =============================================================================
# PostgresDocStore — SQL shape, mocked connection
# =============================================================================


def _pg_store(**cfg_kw):
    from operonx.providers.doc_stores.postgres import PostgresDocStore

    cfg_kw.setdefault("dsn", "postgresql://u@h/db")
    cfg_kw.setdefault("collection", "docs")
    with patch("operonx.providers.doc_stores.postgres.get_pool", return_value=Mock()):
        return PostgresDocStore(DocStoreConfig(**cfg_kw))


class _CapturedSQL:
    """Captures the SQL + params a store executes, returning canned rows."""

    def __init__(self, rows):
        self.rows = rows
        self.sql = None
        self.params = None

    def install(self, store):
        cur = AsyncMock()
        cur.execute = AsyncMock(side_effect=self._capture)
        cur.fetchall = AsyncMock(return_value=self.rows)

        cur_cm = Mock()
        cur_cm.__aenter__ = AsyncMock(return_value=cur)
        cur_cm.__aexit__ = AsyncMock(return_value=False)

        conn = Mock()
        conn.cursor = Mock(return_value=cur_cm)
        conn_cm = Mock()
        conn_cm.__aenter__ = AsyncMock(return_value=conn)
        conn_cm.__aexit__ = AsyncMock(return_value=False)

        store._pool.open = AsyncMock()
        store._pool.connection = Mock(return_value=conn_cm)
        return self

    async def _capture(self, sql, params=None):
        self.sql = sql
        self.params = params


class TestPostgresDocStore:
    @pytest.mark.asyncio
    async def test_uses_any_for_a_stable_statement(self):
        store = _pg_store()
        cap = _CapturedSQL([{"id": 1}]).install(store)
        await store.fetch([1, 2, 3], collection="docs")

        # ANY($1) keeps one statement text regardless of batch size —
        # one plan-cache entry instead of one per id count.
        assert "= ANY(%(ids)s)" in cap.sql
        assert cap.params["ids"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_projection_applied(self):
        store = _pg_store()
        cap = _CapturedSQL([{"id": 1}]).install(store)
        await store.fetch([1], collection="docs", fields=["title", "body"])
        assert "SELECT title, body" in cap.sql

    @pytest.mark.asyncio
    async def test_id_column_forced_into_projection(self):
        store = _pg_store()
        cap = _CapturedSQL([{"id": 1}]).install(store)
        await store.fetch([1], collection="docs", fields=["title"])
        # Without the id column the base class cannot match rows to ids.
        assert "id" in cap.sql.split("FROM")[0]

    @pytest.mark.asyncio
    async def test_star_when_no_fields(self):
        store = _pg_store()
        cap = _CapturedSQL([{"id": 1}]).install(store)
        await store.fetch([1], collection="docs")
        assert "SELECT *" in cap.sql

    @pytest.mark.asyncio
    async def test_collection_falls_back_to_config(self):
        store = _pg_store(collection="fallback_tbl")
        cap = _CapturedSQL([{"id": 1}]).install(store)
        await store.fetch([1])
        assert "FROM fallback_tbl" in cap.sql

    @pytest.mark.asyncio
    async def test_no_table_anywhere_raises(self):
        store = _pg_store(collection=None)
        _CapturedSQL([]).install(store)
        with pytest.raises(ValueError, match="needs a table"):
            await store.fetch([1])

    @pytest.mark.asyncio
    async def test_injection_attempt_in_collection_raises(self):
        store = _pg_store()
        _CapturedSQL([]).install(store)
        with pytest.raises(ValueError, match="Invalid collection identifier"):
            await store.fetch([1], collection="docs; DROP TABLE users--")

    @pytest.mark.asyncio
    async def test_injection_attempt_in_fields_raises(self):
        store = _pg_store()
        _CapturedSQL([]).install(store)
        with pytest.raises(ValueError, match="Invalid fields identifier"):
            await store.fetch([1], collection="docs", fields=["title, (SELECT pw FROM users)"])

    @pytest.mark.asyncio
    async def test_rows_come_back_in_id_order(self):
        store = _pg_store()
        _CapturedSQL([{"id": 3}, {"id": 1}, {"id": 2}]).install(store)
        rows, missing = await store.fetch([1, 2, 3], collection="docs")
        assert [r["id"] for r in rows] == [1, 2, 3]
        assert missing == []


# =============================================================================
# DocFetchOp
# =============================================================================


def _patched_hub(backend):
    hub = Mock()
    hub.get = Mock(return_value=backend)
    return patch("operonx.providers.ops.doc_fetch.resolve_hub", return_value=hub), hub


class TestDocFetchOp:
    def test_type_and_schema(self):
        from operonx.providers.ops import DocFetchOp

        op = DocFetchOp(name="fetch", resource="main")
        assert op.type == "doc-fetch"
        assert set(op.inputs) >= {"ids", "collection", "fields", "id_field"}
        assert set(op.outputs) >= {"rows", "missing"}

    def test_bare_resource_gets_category_prefix(self):
        from operonx.providers.ops import DocFetchOp

        op = DocFetchOp(name="fetch", resource="main")
        backend = _StubStore([])
        patcher, hub = _patched_hub(backend)
        with patcher:
            op._ensure_initialized()
        hub.get.assert_called_once_with("doc_store:main")

    def test_explicit_key_passes_through(self):
        from operonx.providers.ops import DocFetchOp

        op = DocFetchOp(name="fetch", resource="doc_store:main")
        backend = _StubStore([])
        patcher, hub = _patched_hub(backend)
        with patcher:
            op._ensure_initialized()
        hub.get.assert_called_once_with("doc_store:main")

    @pytest.mark.asyncio
    async def test_returns_rows_in_id_order_and_missing(self):
        from operonx.providers.ops import DocFetchOp

        backend = _StubStore([{"id": 1}, {"id": 2}])
        op = DocFetchOp(name="fetch", resource="main")
        patcher, _ = _patched_hub(backend)
        with patcher:
            out = await op._process(ids=[2, 1, 99])

        assert [r["id"] for r in out["rows"]] == [2, 1]
        assert out["missing"] == [99]

    @pytest.mark.asyncio
    async def test_id_field_falls_back_to_resource_config(self):
        from operonx.providers.ops import DocFetchOp

        backend = _StubStore([], config=DocStoreConfig(dsn="x", id_field="doc_key"))
        op = DocFetchOp(name="fetch", resource="main")
        patcher, _ = _patched_hub(backend)
        with patcher:
            await op._process(ids=[1])
        assert backend.calls[0]["id_field"] == "doc_key"

    @pytest.mark.asyncio
    async def test_explicit_id_field_wins(self):
        from operonx.providers.ops import DocFetchOp

        backend = _StubStore([], config=DocStoreConfig(dsn="x", id_field="doc_key"))
        op = DocFetchOp(name="fetch", resource="main")
        patcher, _ = _patched_hub(backend)
        with patcher:
            await op._process(ids=[1], id_field="override")
        assert backend.calls[0]["id_field"] == "override"

    def test_adopts_backend_bound(self):
        from operonx.providers.ops import DocFetchOp

        backend = _StubStore([], bound="cpu")
        op = DocFetchOp(name="fetch", resource="main")
        patcher, _ = _patched_hub(backend)
        with patcher:
            op._ensure_initialized()
        assert op.bound == "cpu"

    def test_metadata_degrades_before_resolution(self):
        from operonx.providers.ops import DocFetchOp

        op = DocFetchOp(name="fetch", resource="main")
        meta = op.specific_metadata
        assert meta["store"] == "main"
        assert "backend" not in meta


class TestExports:
    def test_op_exported(self):
        import operonx.providers as p

        assert p.DocFetchOp is not None

    def test_helpers_exported(self):
        import operonx.providers as p

        assert p.reorder_by_ids is not None
        assert p.partition_by_ids is not None

    def test_plugin_registered(self):
        from operonx.providers.registry import doc_store_plugin

        assert doc_store_plugin.is_registered()


# =============================================================================
# MemoryDocStore — zero-infra store of record
# =============================================================================


def _mem(**kw):
    kw.setdefault("api_type", DocStoreType.MEMORY)
    kw.setdefault("collection", "docs")
    return create_doc_store(DocStoreConfig(**kw))


class TestMemoryDocStore:
    def test_bound_is_cpu(self):
        # Pure dict lookups — nothing to await on.
        assert _mem().bound == "cpu"

    def test_seeds_from_config(self):
        store = _mem(documents=[{"id": 1, "t": "a"}])
        assert 1 in store._collections["docs"]

    @pytest.mark.asyncio
    async def test_fetch_preserves_requested_order(self):
        store = _mem(documents=[{"id": i} for i in (1, 2, 3)])
        rows, missing = await store.fetch([3, 1, 2], collection="docs")
        assert [r["id"] for r in rows] == [3, 1, 2]
        assert missing == []

    @pytest.mark.asyncio
    async def test_reports_missing(self):
        store = _mem(documents=[{"id": 1}])
        rows, missing = await store.fetch([1, 99], collection="docs")
        assert [r["id"] for r in rows] == [1]
        assert missing == [99]

    @pytest.mark.asyncio
    async def test_projection(self):
        store = _mem(documents=[{"id": 1, "title": "a", "body": "b"}])
        rows, _ = await store.fetch([1], collection="docs", fields=["title"])
        assert rows[0]["title"] == "a"
        assert "body" not in rows[0]
        # The id must survive projection or the base can't match rows to ids.
        assert rows[0]["id"] == 1

    @pytest.mark.asyncio
    async def test_returns_copies_not_internal_dicts(self):
        store = _mem(documents=[{"id": 1, "t": "orig"}])
        rows, _ = await store.fetch([1], collection="docs")
        rows[0]["t"] = "mutated"
        again, _ = await store.fetch([1], collection="docs")
        assert again[0]["t"] == "orig"

    def test_put_requires_the_id_field(self):
        store = _mem()
        with pytest.raises(ValueError, match="missing its id field"):
            store.put([{"no_id": 1}], collection="docs")

    def test_put_replaces_by_id(self):
        store = _mem(documents=[{"id": 1, "v": "old"}])
        store.put([{"id": 1, "v": "new"}], collection="docs")
        assert store._collections["docs"][1]["v"] == "new"

    @pytest.mark.asyncio
    async def test_unknown_collection_raises(self):
        store = _mem()
        with pytest.raises(ValueError, match="no collection 'nope'"):
            await store.fetch([1], collection="nope")

    @pytest.mark.asyncio
    async def test_custom_id_field(self):
        store = _mem(id_field="doc_key", documents=[{"doc_key": "x", "t": "a"}])
        rows, missing = await store.fetch(["x"], collection="docs", id_field="doc_key")
        assert rows[0]["t"] == "a"
        assert missing == []


# =============================================================================
# The pair, composed — VectorSearchOp → DocFetchOp in a real graph
# =============================================================================


class TestTwoStorePipeline:
    @pytest.mark.asyncio
    async def test_search_then_fetch_stays_score_aligned(self):
        """The whole point of the pair: ids come back ranked, and the rows
        that follow are in the same order — no manual zipping, no silent
        misalignment."""
        from operonx.providers.vector_stores import (
            VectorStoreConfig,
            VectorStoreMetric,
            VectorStoreType,
            create_vector_store,
        )

        # Derived index: vectors + ids, no content.
        index = create_vector_store(
            VectorStoreConfig(api_type=VectorStoreType.FAISS, metric=VectorStoreMetric.IP, dim=4)
        )
        await index.upsert(
            ids=[10, 20, 30],
            vectors=[[1, 0, 0, 0], [0, 1, 0, 0], [0.9, 0.1, 0, 0]],
        )

        # Store of record: the content, deliberately inserted in an order
        # that does not match either id order or score order.
        docs = _mem(
            documents=[
                {"id": 20, "title": "second"},
                {"id": 30, "title": "third"},
                {"id": 10, "title": "first"},
            ]
        )

        ids, scores, _ = await index.search(query_vector=[1, 0, 0, 0], top_k=2)
        assert ids == [10, 30]

        rows, missing = await docs.fetch(ids, collection="docs")
        assert [r["id"] for r in rows] == [10, 30]
        assert [r["title"] for r in rows] == ["first", "third"]
        assert missing == []
        # Rows line up with scores positionally — that is the contract.
        assert len(rows) == len(scores)

    @pytest.mark.asyncio
    async def test_index_drift_surfaces_as_missing(self):
        """An id in the index with no row in the store of record is real
        drift (deleted doc, failed sync) and must be visible."""
        from operonx.providers.vector_stores import (
            VectorStoreConfig,
            VectorStoreType,
            create_vector_store,
        )

        index = create_vector_store(VectorStoreConfig(api_type=VectorStoreType.FAISS, dim=4))
        await index.upsert(ids=[1, 2], vectors=[[1, 0, 0, 0], [0, 1, 0, 0]])

        docs = _mem(documents=[{"id": 1, "title": "only one"}])

        ids, _, _ = await index.search(query_vector=[1, 0, 0, 0], top_k=2)
        rows, missing = await docs.fetch(ids, collection="docs")

        assert [r["id"] for r in rows] == [1]
        assert missing == [2]
