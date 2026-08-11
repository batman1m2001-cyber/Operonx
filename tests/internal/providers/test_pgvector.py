"""Tests for the pgvector store — SQL shape and filter dialect, mocked.

No Postgres required, so these run on every PR.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from operonx.providers.vector_stores import VectorStoreConfig, VectorStoreMetric, VectorStoreType
from operonx.providers.vector_stores._pg import split_filter

pytestmark = pytest.mark.unit


def _store(**kw):
    from operonx.providers.vector_stores.pgvector import PgVectorStore

    kw.setdefault("api_type", VectorStoreType.PGVECTOR)
    kw.setdefault("dsn", "postgresql://u@h/db")
    kw.setdefault("table", "docs_vec")
    kw.setdefault("metric", VectorStoreMetric.IP)
    with patch("operonx.providers.vector_stores.pgvector.get_pool", return_value=Mock()):
        return PgVectorStore(VectorStoreConfig(**kw))


class _Captured:
    """Captures executed SQL + params, returning canned rows."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.sql = None
        self.params = None
        self.batch = None

    def install(self, store):
        cur = AsyncMock()
        cur.execute = AsyncMock(side_effect=self._capture)
        cur.executemany = AsyncMock(side_effect=self._capture_many)
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
        self.sql, self.params = sql, params

    async def _capture_many(self, sql, batch):
        self.sql, self.batch = sql, batch


# =============================================================================
# Filter dialect
# =============================================================================


class TestSplitFilter:
    def test_none_is_no_filter(self):
        assert split_filter(None) == (None, {})

    def test_explicit_sql_with_bound_params(self):
        where, params = split_filter(
            {"where": "tenant = %(t)s AND created_at >= %(s)s", "params": {"t": "acme", "s": 1}}
        )
        assert where == "tenant = %(t)s AND created_at >= %(s)s"
        assert params == {"t": "acme", "s": 1}

    def test_equality_sugar_binds_values(self):
        where, params = split_filter({"tenant": "acme", "doc_type": "faq"})
        assert "tenant = %(f_tenant)s" in where
        assert "doc_type = %(f_doc_type)s" in where
        assert " AND " in where
        # Values are bound, never interpolated into the statement.
        assert params == {"f_tenant": "acme", "f_doc_type": "faq"}

    def test_sugar_rejects_non_identifier_columns(self):
        # The sugar path interpolates column names, so they must be
        # validated — otherwise it's an injection vector.
        with pytest.raises(ValueError, match="Invalid column name"):
            split_filter({"tenant; DROP TABLE users--": "x"})

    def test_string_filter_rejected_with_a_pointer(self):
        # Expression strings are Milvus's dialect. Silently ignoring one
        # here would drop the filter — a tenant-isolation leak.
        with pytest.raises(ValueError, match="must be a dict, not a string"):
            split_filter("tenant == 'acme'")

    def test_non_dict_rejected(self):
        with pytest.raises(ValueError, match="must be a dict"):
            split_filter(["tenant", "acme"])

    def test_where_must_be_a_string(self):
        with pytest.raises(ValueError, match="\"where\"|'where' must be"):
            split_filter({"where": 123})

    def test_params_must_be_a_dict(self):
        with pytest.raises(ValueError, match="params"):
            split_filter({"where": "a = %(a)s", "params": [1]})

    def test_mixed_shape_rejected(self):
        # Half-explicit/half-sugar is ambiguous; refuse rather than guess.
        with pytest.raises(ValueError, match="Unexpected keys alongside 'where'"):
            split_filter({"where": "a = 1", "tenant": "acme"})


# =============================================================================
# Construction
# =============================================================================


class TestConstruction:
    def test_requires_dsn(self):
        from operonx.providers.vector_stores.pgvector import PgVectorStore

        with pytest.raises(ValueError, match="requires dsn="):
            PgVectorStore(VectorStoreConfig(api_type=VectorStoreType.PGVECTOR, table="t"))

    def test_requires_table(self):
        from operonx.providers.vector_stores.pgvector import PgVectorStore

        with pytest.raises(ValueError, match="requires table="):
            PgVectorStore(
                VectorStoreConfig(api_type=VectorStoreType.PGVECTOR, dsn="postgresql://x")
            )

    def test_bound_is_io(self):
        assert _store().bound == "io"

    def test_rejects_injection_in_table(self):
        with pytest.raises(ValueError, match="Invalid table identifier"):
            _store(table="docs; DROP TABLE users--")

    def test_rejects_injection_in_metadata_columns(self):
        with pytest.raises(ValueError, match="Invalid metadata_columns identifier"):
            _store(metadata_columns=["tenant", "(SELECT pw FROM users)"])


# =============================================================================
# Search SQL
# =============================================================================


class TestSearchSQL:
    @pytest.mark.asyncio
    async def test_orders_by_bare_operator_for_index_use(self):
        store = _store(metric=VectorStoreMetric.IP)
        cap = _Captured().install(store)
        await store.search([1.0, 2.0], top_k=5)

        # Ordering by the converted score (e.g. `-(embedding <#> q) DESC`)
        # is equivalent arithmetic but opaque to the planner — it silently
        # drops to a sequential scan. Must order by the bare operator.
        order_by = cap.sql.split("ORDER BY")[1]
        assert "embedding <#> %(q)s::vector" in order_by
        assert "-(" not in order_by

    @pytest.mark.asyncio
    async def test_ip_score_is_negated_back_to_similarity(self):
        store = _store(metric=VectorStoreMetric.IP)
        cap = _Captured().install(store)
        await store.search([1.0], top_k=1)
        # pgvector's <#> is the NEGATIVE inner product; the score we return
        # must match what FAISS reports for the same metric.
        assert "-(embedding <#> %(q)s::vector) AS _score" in cap.sql

    @pytest.mark.asyncio
    async def test_cosine_distance_converted_to_similarity(self):
        store = _store(metric=VectorStoreMetric.COSINE)
        cap = _Captured().install(store)
        await store.search([1.0], top_k=1)
        assert "1 - (embedding <=> %(q)s::vector) AS _score" in cap.sql

    @pytest.mark.asyncio
    async def test_l2_stays_a_distance(self):
        store = _store(metric=VectorStoreMetric.L2)
        cap = _Captured().install(store)
        await store.search([1.0], top_k=1)
        assert "embedding <-> %(q)s::vector" in cap.sql

    @pytest.mark.asyncio
    async def test_vector_rendered_in_pgvector_text_format(self):
        store = _store()
        cap = _Captured().install(store)
        await store.search([1.0, 2.5, -3.0], top_k=1)
        assert cap.params["q"] == "[1.0,2.5,-3.0]"

    @pytest.mark.asyncio
    async def test_top_k_is_bound_not_interpolated(self):
        store = _store()
        cap = _Captured().install(store)
        await store.search([1.0], top_k=42)
        assert "LIMIT %(k)s" in cap.sql
        assert cap.params["k"] == 42

    @pytest.mark.asyncio
    async def test_filter_reaches_the_where_clause(self):
        store = _store()
        cap = _Captured().install(store)
        await store.search([1.0], filter={"tenant": "acme"})
        assert "WHERE tenant = %(f_tenant)s" in cap.sql
        assert cap.params["f_tenant"] == "acme"

    @pytest.mark.asyncio
    async def test_no_where_clause_without_a_filter(self):
        store = _store()
        cap = _Captured().install(store)
        await store.search([1.0])
        assert "WHERE" not in cap.sql

    @pytest.mark.asyncio
    async def test_collection_overrides_table(self):
        store = _store(table="default_tbl")
        cap = _Captured().install(store)
        await store.search([1.0], collection="other_tbl")
        assert "FROM other_tbl" in cap.sql

    @pytest.mark.asyncio
    async def test_metadata_columns_selected_and_returned(self):
        store = _store(metadata_columns=["tenant", "doc_type"])
        _Captured([{"_id": 7, "_score": 0.9, "tenant": "acme", "doc_type": "faq"}]).install(store)
        ids, scores, metadata = await store.search([1.0], top_k=1)
        assert ids == [7]
        assert scores == [0.9]
        assert metadata == [{"tenant": "acme", "doc_type": "faq"}]

    @pytest.mark.asyncio
    async def test_outputs_are_index_aligned(self):
        store = _store(metadata_columns=["tenant"])
        _Captured(
            [
                {"_id": 1, "_score": 0.9, "tenant": "a"},
                {"_id": 2, "_score": 0.8, "tenant": "b"},
            ]
        ).install(store)
        ids, scores, metadata = await store.search([1.0], top_k=2)
        assert len(ids) == len(scores) == len(metadata) == 2

    @pytest.mark.asyncio
    async def test_custom_id_and_vector_columns(self):
        store = _store(id_column="doc_key", vector_column="vec")
        cap = _Captured().install(store)
        await store.search([1.0], top_k=1)
        assert "doc_key AS _id" in cap.sql
        assert "vec <#> %(q)s::vector" in cap.sql


# =============================================================================
# Upsert
# =============================================================================


class TestUpsert:
    @pytest.mark.asyncio
    async def test_on_conflict_updates(self):
        store = _store()
        cap = _Captured().install(store)
        await store.upsert(ids=[1], vectors=[[1.0, 2.0]])
        assert "ON CONFLICT (id) DO UPDATE SET" in cap.sql

    @pytest.mark.asyncio
    async def test_length_mismatch_raises(self):
        store = _store()
        _Captured().install(store)
        with pytest.raises(ValueError, match="ids/vectors length mismatch"):
            await store.upsert(ids=[1, 2], vectors=[[1.0]])

    @pytest.mark.asyncio
    async def test_metadata_length_mismatch_raises(self):
        store = _store(metadata_columns=["tenant"])
        _Captured().install(store)
        with pytest.raises(ValueError, match="ids/metadata length mismatch"):
            await store.upsert(ids=[1, 2], vectors=[[1.0], [2.0]], metadata=[{"tenant": "a"}])

    @pytest.mark.asyncio
    async def test_undeclared_metadata_key_raises(self):
        # Silently dropping a metadata key means the filter that depends on
        # it stops matching later, with no error at write time.
        store = _store(metadata_columns=["tenant"])
        _Captured().install(store)
        with pytest.raises(ValueError, match="not in metadata_columns"):
            await store.upsert(ids=[1], vectors=[[1.0]], metadata=[{"nope": "x"}])

    @pytest.mark.asyncio
    async def test_metadata_written_as_columns(self):
        store = _store(metadata_columns=["tenant"])
        cap = _Captured().install(store)
        await store.upsert(ids=[1], vectors=[[1.0]], metadata=[{"tenant": "acme"}])
        assert cap.batch[0]["tenant"] == "acme"
        assert cap.batch[0]["id"] == 1
        assert cap.batch[0]["embedding"] == "[1.0]"
