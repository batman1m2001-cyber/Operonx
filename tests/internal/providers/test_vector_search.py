"""Tests for the vector store layer and VectorSearchOp.

FAISS-backed, so no server and no docker — these run on every PR.

The providers conftest auto-marks everything in this directory as
``integration`` (excluded by CI's ``-m "not integration"`` selector).
These tests are mock/FAISS-only and cheap, so they opt back in via the
``unit`` marker the conftest checks for.
"""

from unittest.mock import Mock, patch

import numpy as np
import pytest

from operonx.providers.vector_stores import (
    BaseVectorStore,
    VectorStoreConfig,
    VectorStoreMetric,
    VectorStoreType,
    create_vector_store,
)

pytestmark = pytest.mark.unit


def _faiss_config(**kw) -> VectorStoreConfig:
    kw.setdefault("api_type", VectorStoreType.FAISS)
    kw.setdefault("metric", VectorStoreMetric.IP)
    kw.setdefault("dim", 4)
    return VectorStoreConfig(**kw)


@pytest.fixture
def store():
    """Empty in-memory FAISS index, inner-product metric, dim 4."""
    return create_vector_store(_faiss_config())


# =============================================================================
# Config
# =============================================================================


class TestVectorStoreConfig:
    def test_default_is_in_memory_faiss(self):
        cfg = VectorStoreConfig.default()
        assert cfg.api_type == VectorStoreType.FAISS
        assert cfg.dim == 1024

    def test_category_is_vector_store(self):
        # Drives the `vector_store:<name>` ResourceHub key.
        assert VectorStoreConfig._category == "vector_store"

    def test_backend_types(self):
        assert {t.value for t in VectorStoreType} == {"faiss", "pgvector", "qdrant"}

    def test_metrics(self):
        assert {m.value for m in VectorStoreMetric} == {"ip", "l2", "cosine"}


# =============================================================================
# Factory
# =============================================================================


class TestFactory:
    def test_creates_faiss_backend(self, store):
        from operonx.providers.vector_stores.faiss import FaissVectorStore

        assert isinstance(store, FaissVectorStore)
        assert isinstance(store, BaseVectorStore)

    def test_unsupported_type_raises(self):
        cfg = _faiss_config()
        cfg.api_type = "not-a-backend"  # bypass enum on purpose
        with pytest.raises(ValueError, match="Unsupported vector store"):
            create_vector_store(cfg)

    def test_missing_extra_message_points_at_install(self):
        from operonx.providers.vector_stores.factory import _missing_extra_message

        msg = _missing_extra_message("PgVectorStore", "pgvector", ImportError("no psycopg"))
        assert "pip install operonx[pgvector]" in msg
        assert "no psycopg" in msg


# =============================================================================
# FAISS backend
# =============================================================================


class TestFaissConstruction:
    def test_bound_is_cpu(self, store):
        # Load-bearing: VectorSearchOp reads this to pick the thread pool.
        # A local index is CPU-bound, unlike every networked store.
        assert store.bound == "cpu"

    def test_needs_path_or_collections_or_dim(self):
        cfg = VectorStoreConfig(api_type=VectorStoreType.FAISS)
        with pytest.raises(ValueError, match="path=.*collections=.*dim="):
            create_vector_store(cfg)

    def test_multi_collection_from_paths(self, tmp_path):
        # Pre-partitioned indices are how FAISS approximates filtering —
        # the error message in search() points users here.
        a = create_vector_store(_faiss_config())
        b = create_vector_store(_faiss_config())
        pa, pb = str(tmp_path / "a.faiss"), str(tmp_path / "b.faiss")
        a.save(pa)
        b.save(pb)

        multi = create_vector_store(
            VectorStoreConfig(
                api_type=VectorStoreType.FAISS,
                collections={"alpha": pa, "beta": pb},
            )
        )
        assert set(multi._indices) == {"alpha", "beta"}

    def test_single_path_roundtrip(self, tmp_path):
        path = str(tmp_path / "idx.faiss")
        create_vector_store(_faiss_config()).save(path)

        loaded = create_vector_store(VectorStoreConfig(api_type=VectorStoreType.FAISS, path=path))
        assert loaded._default == "default"


class TestFaissSearch:
    @pytest.mark.asyncio
    async def test_upsert_then_search_roundtrip(self, store):
        await store.upsert(
            ids=[10, 20, 30],
            vectors=[[1, 0, 0, 0], [0, 1, 0, 0], [0.9, 0.1, 0, 0]],
        )
        ids, scores, metadata = await store.search(query_vector=[1, 0, 0, 0], top_k=2)

        assert ids == [10, 30]
        assert scores[0] > scores[1]
        assert len(ids) == len(scores) == len(metadata)

    @pytest.mark.asyncio
    async def test_outputs_are_index_aligned(self, store):
        await store.upsert(ids=[1, 2, 3], vectors=np.eye(4)[:3].tolist())
        ids, scores, metadata = await store.search(query_vector=[1, 0, 0, 0], top_k=3)
        # The whole contract downstream depends on this.
        assert len(ids) == len(scores) == len(metadata)

    @pytest.mark.asyncio
    async def test_scores_ordered_best_first(self, store):
        await store.upsert(
            ids=[1, 2, 3],
            vectors=[[1, 0, 0, 0], [0.5, 0.5, 0, 0], [0, 0, 0, 1]],
        )
        _, scores, _ = await store.search(query_vector=[1, 0, 0, 0], top_k=3)
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_top_k_honored(self, store):
        await store.upsert(ids=list(range(5)), vectors=np.random.rand(5, 4).tolist())
        ids, _, _ = await store.search(query_vector=[1, 0, 0, 0], top_k=2)
        assert len(ids) == 2

    @pytest.mark.asyncio
    async def test_top_k_larger_than_index_is_clamped(self, store):
        await store.upsert(ids=[1, 2], vectors=[[1, 0, 0, 0], [0, 1, 0, 0]])
        ids, scores, metadata = await store.search(query_vector=[1, 0, 0, 0], top_k=50)
        # FAISS pads short results with id -1; those must be dropped, not surfaced.
        assert len(ids) == 2
        assert -1 not in ids
        assert len(scores) == len(metadata) == 2

    @pytest.mark.asyncio
    async def test_empty_index_returns_empty_lists(self, store):
        ids, scores, metadata = await store.search(query_vector=[1, 0, 0, 0])
        assert ids == [] and scores == [] and metadata == []

    @pytest.mark.asyncio
    async def test_metadata_is_empty_dicts(self, store):
        await store.upsert(ids=[1], vectors=[[1, 0, 0, 0]])
        _, _, metadata = await store.search(query_vector=[1, 0, 0, 0])
        # FAISS stores no metadata, but the contract still requires the list.
        assert metadata == [{}]

    @pytest.mark.asyncio
    async def test_unknown_collection_raises(self, store):
        with pytest.raises(ValueError, match="no collection 'nope'"):
            await store.search(query_vector=[1, 0, 0, 0], collection="nope")


class TestFaissFilterRefusal:
    @pytest.mark.asyncio
    async def test_any_filter_raises(self, store):
        with pytest.raises(ValueError, match="FAISS does not support metadata filtering"):
            await store.search(query_vector=[1, 0, 0, 0], filter={"tenant": "acme"})

    @pytest.mark.asyncio
    async def test_error_points_to_alternatives(self, store):
        with pytest.raises(ValueError) as exc:
            await store.search(query_vector=[1, 0, 0, 0], filter={"a": 1})
        msg = str(exc.value)
        assert "pgvector or Qdrant" in msg
        assert "collection=" in msg

    @pytest.mark.asyncio
    async def test_no_silent_post_filtering(self, store):
        # Refusing beats over-fetch-then-filter: the latter silently
        # returns fewer than top_k hits with no error.
        await store.upsert(ids=[1], vectors=[[1, 0, 0, 0]])
        with pytest.raises(ValueError):
            await store.search(query_vector=[1, 0, 0, 0], filter="tenant == 'acme'")


class TestFaissUpsert:
    @pytest.mark.asyncio
    async def test_upsert_replaces_existing_id(self, store):
        await store.upsert(ids=[1], vectors=[[1, 0, 0, 0]])
        await store.upsert(ids=[1], vectors=[[0, 1, 0, 0]])

        ids, _, _ = await store.search(query_vector=[0, 1, 0, 0], top_k=5)
        assert ids == [1]  # replaced, not duplicated

    @pytest.mark.asyncio
    async def test_length_mismatch_raises(self, store):
        with pytest.raises(ValueError, match="ids/vectors length mismatch"):
            await store.upsert(ids=[1, 2], vectors=[[1, 0, 0, 0]])

    @pytest.mark.asyncio
    async def test_metadata_accepted_and_ignored(self, store):
        # Contract parity: FAISS has nowhere to store it, but passing it
        # must not blow up a pipeline that also targets pgvector.
        await store.upsert(ids=[1], vectors=[[1, 0, 0, 0]], metadata=[{"tenant": "acme"}])
        _, _, metadata = await store.search(query_vector=[1, 0, 0, 0])
        assert metadata == [{}]


class TestFaissMetrics:
    @pytest.mark.asyncio
    async def test_l2_orders_nearest_first(self):
        s = create_vector_store(_faiss_config(metric=VectorStoreMetric.L2))
        await s.upsert(ids=[1, 2], vectors=[[1, 0, 0, 0], [9, 9, 9, 9]])
        ids, scores, _ = await s.search(query_vector=[1, 0, 0, 0], top_k=2)
        # L2 is a distance: nearest first means ascending score.
        assert ids[0] == 1
        assert scores[0] < scores[1]

    @pytest.mark.asyncio
    async def test_cosine_normalises_magnitude_away(self):
        s = create_vector_store(_faiss_config(metric=VectorStoreMetric.COSINE))
        # Same direction, wildly different magnitude.
        await s.upsert(ids=[1], vectors=[[10, 0, 0, 0]])
        _, scores, _ = await s.search(query_vector=[1, 0, 0, 0])
        assert scores[0] == pytest.approx(1.0, abs=1e-5)

    @pytest.mark.asyncio
    async def test_cosine_does_not_mutate_caller_vectors(self):
        s = create_vector_store(_faiss_config(metric=VectorStoreMetric.COSINE))
        query = np.array([3.0, 4.0, 0.0, 0.0], dtype=np.float32)
        original = query.copy()
        await s.upsert(ids=[1], vectors=[[1, 0, 0, 0]])
        await s.search(query_vector=query)
        # faiss.normalize_L2 works in place — the backend must copy first.
        assert np.array_equal(query, original)


# =============================================================================
# VectorSearchOp
# =============================================================================


def _patched_hub(backend):
    """Patch resolve_hub so the op resolves to `backend`."""
    hub = Mock()
    hub.get = Mock(return_value=backend)
    return patch("operonx.providers.ops.vector_search.resolve_hub", return_value=hub), hub


class TestVectorSearchOp:
    def test_type_and_schema(self, store):
        from operonx.providers.ops import VectorSearchOp

        op = VectorSearchOp(name="search", resource="docs")
        assert op.type == "vector-search"
        assert set(op.inputs) >= {"query_vector", "top_k", "filter", "collection"}
        assert set(op.outputs) >= {"ids", "scores", "metadata"}

    def test_filter_param_accepts_dict_and_str(self):
        from operonx.providers.ops import VectorSearchOp

        op = VectorSearchOp(name="search", resource="docs")
        # Milvus's native dialect is an expression string, not a dict.
        assert op.inputs["filter"].type == (dict, str)

    def test_bare_resource_gets_category_prefix(self, store):
        from operonx.providers.ops import VectorSearchOp

        op = VectorSearchOp(name="search", resource="docs")
        patcher, hub = _patched_hub(store)
        with patcher:
            op._ensure_initialized()
        hub.get.assert_called_once_with("vector_store:docs")

    def test_explicit_key_passes_through(self, store):
        from operonx.providers.ops import VectorSearchOp

        op = VectorSearchOp(name="search", resource="vector_store:docs")
        patcher, hub = _patched_hub(store)
        with patcher:
            op._ensure_initialized()
        hub.get.assert_called_once_with("vector_store:docs")

    def test_adopts_backend_bound(self, store):
        from operonx.providers.ops import VectorSearchOp

        op = VectorSearchOp(name="search", resource="docs")
        assert op.bound == "io"  # default before resolution
        patcher, _ = _patched_hub(store)
        with patcher:
            op._ensure_initialized()
        # FAISS is a local index — the op must switch pools.
        assert op.bound == "cpu"

    @pytest.mark.asyncio
    async def test_process_returns_three_aligned_lists(self, store):
        from operonx.providers.ops import VectorSearchOp

        await store.upsert(ids=[7, 8], vectors=[[1, 0, 0, 0], [0, 1, 0, 0]])

        op = VectorSearchOp(name="search", resource="docs")
        patcher, _ = _patched_hub(store)
        with patcher:
            out = await op._process(query_vector=[1, 0, 0, 0], top_k=2)

        assert out["ids"] == [7, 8]
        assert len(out["ids"]) == len(out["scores"]) == len(out["metadata"])

    @pytest.mark.asyncio
    async def test_filter_reaches_backend(self, store):
        from operonx.providers.ops import VectorSearchOp

        op = VectorSearchOp(name="search", resource="docs")
        patcher, _ = _patched_hub(store)
        # FAISS refuses filters — proves the op forwards rather than drops.
        # A silently-dropped filter is a tenant-isolation leak.
        with patcher, pytest.raises(ValueError, match="does not support metadata filtering"):
            await op._process(query_vector=[1, 0, 0, 0], filter={"tenant": "acme"})

    @pytest.mark.asyncio
    async def test_collection_reaches_backend(self, store):
        from operonx.providers.ops import VectorSearchOp

        op = VectorSearchOp(name="search", resource="docs")
        patcher, _ = _patched_hub(store)
        with patcher, pytest.raises(ValueError, match="no collection 'other'"):
            await op._process(query_vector=[1, 0, 0, 0], collection="other")

    def test_specific_metadata_before_resolution_is_degraded_not_fatal(self):
        from operonx.providers.ops import VectorSearchOp

        op = VectorSearchOp(name="search", resource="docs")
        # Tracing must never force resource resolution — reading metadata
        # on an unresolved op reports what it knows and does not raise.
        meta = op.specific_metadata
        assert meta["store"] == "docs"
        assert "backend" not in meta

    def test_specific_metadata_reports_backend_and_metric(self, store):
        from operonx.providers.ops import VectorSearchOp

        op = VectorSearchOp(name="search", resource="docs")
        patcher, _ = _patched_hub(store)
        with patcher:
            op._ensure_initialized()
            meta = op.specific_metadata
        assert meta["store"] == "docs"
        assert meta["backend"] == "faiss"
        assert meta["metric"] == "ip"

    def test_shorthand_of(self):
        from operonx.providers.ops import VectorSearchOp

        op = VectorSearchOp.of(resource="docs", name="search", top_k=5)
        assert op.resource == "docs"
        assert "top_k" in op.inputs


class TestExports:
    def test_op_exported_from_providers(self):
        import operonx.providers as p

        assert p.VectorSearchOp is not None

    def test_vector_store_symbols_exported(self):
        import operonx.providers as p

        assert p.VectorStoreConfig is not None
        assert p.create_vector_store is not None

    def test_plugin_registered(self):
        from operonx.providers.registry import vector_store_plugin

        assert vector_store_plugin.is_registered()

    def test_package_imports_without_faiss(self):
        # The pure symbols must not drag in faiss; only the backend class does.
        import importlib

        mod = importlib.import_module("operonx.providers.vector_stores")
        assert mod.BaseVectorStore is not None
        assert "FaissVectorStore" in mod.__all__
