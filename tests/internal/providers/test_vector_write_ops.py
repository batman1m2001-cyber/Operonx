"""VectorUpsertOp, VectorDeleteOp, and a search on an empty index.

The write half of the retrieval pair. FAISS-backed, so no server: the
backends' own behaviour is pinned by ``test_vector_store_contract.py``;
this file checks what the ops add — resolution, the ``bound`` they
adopt, their ports, and that a graph can keep an index in step with its
store of record.
"""

import logging
import textwrap
from unittest.mock import Mock, patch

import pytest

from operonx import END, START, Operon, graph
from operonx.core.registry import ResourceHub
from operonx.providers.ops import VectorDeleteOp, VectorSearchOp, VectorUpsertOp
from operonx.providers.vector_stores import (
    VectorStoreConfig,
    VectorStoreMetric,
    VectorStoreType,
    create_vector_store,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def store():
    return create_vector_store(
        VectorStoreConfig(api_type=VectorStoreType.FAISS, metric=VectorStoreMetric.IP, dim=4)
    )


def _hub_for(backend):
    hub = Mock()
    hub.get = Mock(return_value=backend)
    return patch("operonx.providers.ops.vector_search.resolve_hub", return_value=hub), hub


@pytest.fixture
def faiss_hub(tmp_path, monkeypatch):
    """A real hub with ``vector_store:docs`` (an empty FAISS index), for graphs."""
    path = tmp_path / "resources.yaml"
    path.write_text(
        textwrap.dedent("""\
            vector_store:docs:
              api_type: faiss
              metric: ip
              dim: 4
        """),
        encoding="utf-8",
    )
    monkeypatch.setattr(ResourceHub, "_instance", ResourceHub.from_yaml(path))
    return ResourceHub.instance()


# =============================================================================
# The ops' shape
# =============================================================================


@pytest.mark.parametrize(
    "cls, kind, inputs, outputs",
    [
        (
            VectorUpsertOp,
            "vector-upsert",
            {"ids", "vectors", "metadata", "collection"},
            {"upserted"},
        ),
        (VectorDeleteOp, "vector-delete", {"ids", "filter", "collection"}, {"deleted"}),
    ],
)
def test_ports_and_type(cls, kind, inputs, outputs):
    op = cls(name="w", resource="docs")
    assert op.type == kind
    assert set(op.inputs) == inputs
    assert set(op.outputs) == outputs
    assert op.show_keys == tuple(outputs)


@pytest.mark.parametrize("cls", [VectorUpsertOp, VectorDeleteOp])
def test_resolution_and_bound_follow_the_search_op(cls, store):
    op = cls(name="w", resource="docs")
    assert op.bound == "io"
    patcher, hub = _hub_for(store)
    with patcher:
        op._ensure_initialized()
    hub.get.assert_called_once_with("vector_store:docs")
    assert op.bound == "cpu"  # FAISS is an in-process index


def test_delete_filter_accepts_every_native_dialect():
    assert VectorDeleteOp(name="d", resource="docs").inputs["filter"].type == (dict, str)


def test_specific_metadata_names_the_store_and_backend(store):
    op = VectorUpsertOp(name="w", resource="docs")
    assert op.specific_metadata == {"store": "docs"}  # tracing never forces resolution
    patcher, _ = _hub_for(store)
    with patcher:
        op._ensure_initialized()
    assert op.specific_metadata == {"store": "docs", "backend": "faiss", "metric": "ip"}


# =============================================================================
# What they do
# =============================================================================


async def test_upsert_writes_and_reports_the_count(store):
    patcher, _ = _hub_for(store)
    with patcher:
        out = await VectorUpsertOp(name="w", resource="docs")._process(
            ids=[1, 2], vectors=[[1, 0, 0, 0], [0, 1, 0, 0]]
        )
    assert out == {"upserted": 2}
    ids, _, _ = await store.search(query_vector=[0, 1, 0, 0], top_k=1)
    assert ids == [2]


async def test_delete_removes_and_reports_the_count(store):
    await store.upsert(ids=[1, 2, 3], vectors=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]])
    patcher, _ = _hub_for(store)
    with patcher:
        out = await VectorDeleteOp(name="d", resource="docs")._process(ids=[2, 9])
    assert out == {"deleted": 1}
    ids, _, _ = await store.search(query_vector=[1, 1, 1, 0], top_k=5)
    assert sorted(ids) == [1, 3]


async def test_delete_forwards_the_filter_rather_than_dropping_it(store):
    """A dropped filter would turn "delete tenant acme" into "delete these ids"
    or worse; FAISS refusing it proves the op passed it on."""
    patcher, _ = _hub_for(store)
    with patcher, pytest.raises(ValueError, match="does not support metadata filtering"):
        await VectorDeleteOp(name="d", resource="docs")._process(filter={"tenant": "acme"})


async def test_delete_with_nothing_to_select_is_refused(store):
    patcher, _ = _hub_for(store)
    with patcher, pytest.raises(ValueError, match="ids= or filter="):
        await VectorDeleteOp(name="d", resource="docs")._process()


async def test_a_graph_keeps_the_index_in_step_with_deletes(faiss_hub):
    """Write, delete, search — the GC step of an ingest pipeline."""

    @graph
    def sync(ids, vectors, gone, query):
        write = VectorUpsertOp.of(resource="docs", ids=ids, vectors=vectors)
        drop = VectorDeleteOp.of(resource="docs", ids=gone)
        hits = VectorSearchOp.of(resource="docs", query_vector=query, top_k=5)
        START >> write >> drop >> hits >> END

    params = {"ids": None, "vectors": None, "gone": None, "query": None}
    out = await Operon(sync, params=params).run(
        inputs={
            "ids": [1, 2, 3],
            "vectors": [[1, 0, 0, 0], [0.9, 0.1, 0, 0], [0, 1, 0, 0]],
            "gone": [2],
            "query": [1, 0, 0, 0],
        }
    )
    assert "$errors" not in out
    assert out["ids"] == [1, 3]
    assert out["empty_index"] is False


# =============================================================================
# Searching an empty index is not silent
# =============================================================================


async def test_an_unfiltered_search_of_an_empty_index_warns_and_flags(store, caplog):
    patcher, _ = _hub_for(store)
    with patcher, caplog.at_level(logging.WARNING):
        out = await VectorSearchOp(name="s", resource="docs")._process(query_vector=[1, 0, 0, 0])
    assert out["ids"] == [] and out["empty_index"] is True
    [record] = [r for r in caplog.records if "holds no vectors" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert "vector_store:docs" in record.getMessage()


async def test_hits_are_not_flagged(store, caplog):
    await store.upsert(ids=[1], vectors=[[1, 0, 0, 0]])
    patcher, _ = _hub_for(store)
    with patcher, caplog.at_level(logging.WARNING):
        out = await VectorSearchOp(name="s", resource="docs")._process(query_vector=[1, 0, 0, 0])
    assert out["empty_index"] is False
    assert not [r for r in caplog.records if "holds no vectors" in r.getMessage()]


async def test_no_hits_under_a_filter_is_an_answer_not_an_empty_index(caplog):
    """A filter that matches nothing is a legitimate empty result: the
    index may be full of other tenants' vectors."""
    backend = Mock(bound="io")

    async def search(**_):
        return [], [], []

    backend.search = search
    patcher, _ = _hub_for(backend)
    with patcher, caplog.at_level(logging.WARNING):
        out = await VectorSearchOp(name="s", resource="docs")._process(
            query_vector=[1, 0, 0, 0], filter={"tenant": "nobody"}
        )
    assert out["empty_index"] is False
    assert not [r for r in caplog.records if "holds no vectors" in r.getMessage()]


async def test_an_empty_index_is_flagged_in_a_graph_run(faiss_hub):
    @graph
    def ask(query):
        hits = VectorSearchOp.of(resource="docs", query_vector=query)
        START >> hits >> END

    out = await Operon(ask, params={"query": None}).run(inputs={"query": [1, 0, 0, 0]})
    assert "$errors" not in out
    assert out["ids"] == [] and out["empty_index"] is True
