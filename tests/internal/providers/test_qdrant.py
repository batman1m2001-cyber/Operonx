"""Tests for the Qdrant vector store — mocked client, no server needed."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from operonx.providers.vector_stores import VectorStoreConfig, VectorStoreType

pytestmark = pytest.mark.unit


def _store(**kw):
    from operonx.providers.vector_stores.qdrant import QdrantVectorStore, _reset_client_cache

    kw.setdefault("api_type", VectorStoreType.QDRANT)
    kw.setdefault("url", "http://localhost:6333")
    kw.setdefault("collection", "docs")
    _reset_client_cache()
    with patch("operonx.providers.vector_stores.qdrant._get_client", return_value=Mock()):
        return QdrantVectorStore(VectorStoreConfig(**kw))


def _hit(id_, score, payload=None):
    return SimpleNamespace(id=id_, score=score, payload=payload or {})


def _respond(store, points):
    store._client.query_points = AsyncMock(return_value=SimpleNamespace(points=points))
    return store._client.query_points


# =============================================================================
# Construction
# =============================================================================


class TestConstruction:
    def test_requires_url(self):
        from operonx.providers.vector_stores.qdrant import QdrantVectorStore

        with pytest.raises(ValueError, match="requires url="):
            QdrantVectorStore(VectorStoreConfig(api_type=VectorStoreType.QDRANT, collection="d"))

    def test_requires_collection(self):
        from operonx.providers.vector_stores.qdrant import QdrantVectorStore

        with pytest.raises(ValueError, match="requires collection="):
            QdrantVectorStore(
                VectorStoreConfig(api_type=VectorStoreType.QDRANT, url="http://x:6333")
            )

    def test_bound_is_io(self):
        assert _store().bound == "io"

    def test_payload_unrestricted_by_default(self):
        # No metadata_columns → Qdrant returns the whole payload.
        assert _store()._payload_keys is True

    def test_metadata_columns_restrict_returned_payload(self):
        # Declaring them is how document content is kept out of a derived
        # index — otherwise the whole payload comes back.
        assert _store(metadata_columns=["tenant", "doc_type"])._payload_keys == [
            "tenant",
            "doc_type",
        ]


class TestClientCache:
    def test_same_settings_reuse_one_client(self):
        from operonx.providers.vector_stores.qdrant import _get_client, _reset_client_cache

        _reset_client_cache()
        with patch("qdrant_client.AsyncQdrantClient") as ctor:
            ctor.return_value = Mock()
            a = _get_client("http://h:6333", None)
            b = _get_client("http://h:6333", None)
        assert a is b
        assert ctor.call_count == 1

    def test_different_settings_get_different_clients(self):
        from operonx.providers.vector_stores.qdrant import _get_client, _reset_client_cache

        _reset_client_cache()
        with patch("qdrant_client.AsyncQdrantClient") as ctor:
            ctor.side_effect = [Mock(), Mock()]
            a = _get_client("http://h:6333", None)
            b = _get_client("http://h:6333", "secret")
        assert a is not b


# =============================================================================
# Filters — native condition tree, validated
# =============================================================================


class TestFilters:
    def test_none_passes_through(self):
        assert _store()._to_filter(None) is None

    def test_native_condition_tree_accepted(self):
        f = _store()._to_filter(
            {
                "must": [
                    {"key": "tenant", "match": {"value": "acme"}},
                    {"key": "created_at", "range": {"gte": 1700000000}},
                    {"key": "doc_type", "match": {"any": ["faq", "manual"]}},
                ]
            }
        )
        assert len(f.must) == 3

    def test_should_and_must_not_supported(self):
        f = _store()._to_filter(
            {
                "should": [{"key": "a", "match": {"value": 1}}],
                "must_not": [{"key": "b", "match": {"value": 2}}],
            }
        )
        assert f.should and f.must_not

    def test_unrecognised_shape_raises(self):
        # Must never degrade to "no filter" — that returns MORE rows,
        # which in a multi-tenant system is a data leak, not a warning.
        with pytest.raises(ValueError, match="Invalid Qdrant filter"):
            _store()._to_filter({"totally_bogus": 1})

    def test_expression_string_rejected_with_a_pointer(self):
        with pytest.raises(ValueError, match="not a string"):
            _store()._to_filter('tenant == "acme"')

    def test_non_dict_rejected(self):
        with pytest.raises(ValueError, match="must be a dict"):
            _store()._to_filter(["tenant", "acme"])

    def test_prebuilt_filter_object_passes_through(self):
        from qdrant_client import models

        native = models.Filter(
            must=[models.FieldCondition(key="a", match=models.MatchValue(value=1))]
        )
        assert _store()._to_filter(native) is native


# =============================================================================
# Search
# =============================================================================


class TestSearch:
    @pytest.mark.asyncio
    async def test_returns_three_aligned_lists(self):
        store = _store(metadata_columns=["tenant"])
        _respond(store, [_hit(7, 0.9, {"tenant": "acme"}), _hit(8, 0.7, {"tenant": "acme"})])

        ids, scores, metadata = await store.search([1.0, 2.0], top_k=2)
        assert ids == [7, 8]
        assert scores == [0.9, 0.7]
        assert metadata == [{"tenant": "acme"}, {"tenant": "acme"}]
        assert len(ids) == len(scores) == len(metadata)

    @pytest.mark.asyncio
    async def test_empty_result(self):
        store = _store()
        _respond(store, [])
        assert await store.search([1.0]) == ([], [], [])

    @pytest.mark.asyncio
    async def test_missing_payload_becomes_empty_dict(self):
        store = _store()
        _respond(store, [_hit(1, 0.5, None)])
        _, _, metadata = await store.search([1.0])
        assert metadata == [{}]

    @pytest.mark.asyncio
    async def test_query_args_forwarded(self):
        store = _store(metadata_columns=["tenant"])
        spy = _respond(store, [])
        await store.search([1.0, 2.0], top_k=17, collection="other")

        kwargs = spy.call_args.kwargs
        assert kwargs["collection_name"] == "other"
        assert kwargs["query"] == [1.0, 2.0]
        assert kwargs["limit"] == 17
        assert kwargs["with_payload"] == ["tenant"]

    @pytest.mark.asyncio
    async def test_filter_reaches_the_client(self):
        store = _store()
        spy = _respond(store, [])
        await store.search([1.0], filter={"must": [{"key": "t", "match": {"value": "a"}}]})
        assert spy.call_args.kwargs["query_filter"] is not None

    @pytest.mark.asyncio
    async def test_collection_falls_back_to_config(self):
        store = _store(collection="configured")
        spy = _respond(store, [])
        await store.search([1.0])
        assert spy.call_args.kwargs["collection_name"] == "configured"


# =============================================================================
# Upsert
# =============================================================================


class TestUpsert:
    @pytest.mark.asyncio
    async def test_builds_points(self):
        store = _store()
        store._client.upsert = AsyncMock()
        await store.upsert(ids=[1, 2], vectors=[[1.0], [2.0]], metadata=[{"a": 1}, {"a": 2}])

        points = store._client.upsert.call_args.kwargs["points"]
        assert [p.id for p in points] == [1, 2]
        assert [p.payload for p in points] == [{"a": 1}, {"a": 2}]

    @pytest.mark.asyncio
    async def test_length_mismatch_raises(self):
        store = _store()
        store._client.upsert = AsyncMock()
        with pytest.raises(ValueError, match="ids/vectors length mismatch"):
            await store.upsert(ids=[1, 2], vectors=[[1.0]])

    @pytest.mark.asyncio
    async def test_metadata_length_mismatch_raises(self):
        store = _store()
        store._client.upsert = AsyncMock()
        with pytest.raises(ValueError, match="ids/metadata length mismatch"):
            await store.upsert(ids=[1, 2], vectors=[[1.0], [2.0]], metadata=[{"a": 1}])

    @pytest.mark.asyncio
    async def test_undeclared_metadata_key_raises(self):
        # Dropping it silently means the filter that depends on it stops
        # matching later, with no error at write time.
        store = _store(metadata_columns=["tenant"])
        store._client.upsert = AsyncMock()
        with pytest.raises(ValueError, match="not in metadata_columns"):
            await store.upsert(ids=[1], vectors=[[1.0]], metadata=[{"nope": 1}])

    @pytest.mark.asyncio
    async def test_payload_free_upsert_allowed(self):
        store = _store(metadata_columns=["tenant"])
        store._client.upsert = AsyncMock()
        await store.upsert(ids=[1], vectors=[[1.0]])
        assert store._client.upsert.call_args.kwargs["points"][0].payload == {}


class TestFactoryAndExports:
    def test_factory_dispatches_to_qdrant(self):
        from operonx.providers.vector_stores import create_vector_store
        from operonx.providers.vector_stores.qdrant import QdrantVectorStore, _reset_client_cache

        _reset_client_cache()
        with patch("operonx.providers.vector_stores.qdrant._get_client", return_value=Mock()):
            store = create_vector_store(
                VectorStoreConfig(
                    api_type=VectorStoreType.QDRANT,
                    url="http://localhost:6333",
                    collection="docs",
                )
            )
        assert isinstance(store, QdrantVectorStore)

    def test_exported_from_providers(self):
        import operonx.providers as p

        assert p.QdrantVectorStore is not None
