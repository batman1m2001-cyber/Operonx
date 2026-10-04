"""The Catalog contract, run against every catalog.

SQLite always; Postgres when ``KB_TEST_PG_DSN`` names a database (each test
gets a throwaway schema, dropped afterwards).
"""

import os
import threading
import uuid

import pytest

from operonx_kb.errors import CatalogError
from operonx_kb.model.collection import Collection, CollectionSpec
from operonx_kb.model.document import Chunk, Document, DocumentVersion, VersionChunk
from operonx_kb.model.ids import sha256_text
from operonx_kb.parsing.base import ParsedDoc, RawBlock
from operonx_kb.stores.catalog.sql import migrations
from operonx_kb.stores.catalog.sqlite import SqliteCatalog
from operonx_kb.structure.build import build_version

PG_DSN = os.environ.get("KB_TEST_PG_DSN")
BACKENDS = [
    "sqlite",
    pytest.param("postgres", marks=pytest.mark.skipif(not PG_DSN, reason="set KB_TEST_PG_DSN")),
]


def _make(kind, tmp_path):
    if kind == "sqlite":
        return SqliteCatalog(tmp_path / "c.db")
    from operonx_kb.stores.catalog.postgres import PostgresCatalog

    return PostgresCatalog(PG_DSN, schema=f"kbtest_{uuid.uuid4().hex[:12]}")


@pytest.fixture(params=BACKENDS)
def cat(request, tmp_path):
    c = _make(request.param, tmp_path)
    c.put_collection(Collection(id="col", spec=CollectionSpec()))
    yield c
    if request.param == "postgres":
        c.drop_schema()
        c.close()


def _version(vid, texts):
    tree = build_version(ParsedDoc(blocks=[RawBlock(kind="paragraph", text=t) for t in texts]), vid)
    chunks, occ = [], []
    for i, e in enumerate(e for e in tree.elements if e.kind == "paragraph"):
        cid = f"ch_{sha256_text(e.text)[:32]}"
        chunks.append(Chunk(id=cid, document_id="doc_1", content_sha=sha256_text(e.text), token_count=1, text=e.text,
                            embed_text=e.text, embed_text_sha=sha256_text(e.text)))  # fmt: skip
        occ.append(
            VersionChunk(
                version_id=vid, chunk_id=cid, ordinal=i, spans=[e.span], element_ids=[e.id]
            )
        )
    version = DocumentVersion(
        id=vid,
        document_id="doc_1",
        ordinal=0,
        raw_sha="r" + vid,
        text_sha=tree.text_sha,
        pipeline_fp="fp",
    )
    return tree, version, chunks, occ


DOC = Document(id="doc_1", collection_id="col", key="k", mime="text/plain")


def _commit(cat, vid, texts):
    tree, version, chunks, occ = _version(vid, texts)
    return cat.commit_version(DOC, version, tree.elements, tree.pages, chunks, occ), tree


def test_migrations_are_numbered_idempotent_and_match_across_dialects(cat):
    versions = [v for v, _, _ in migrations(cat.dialect)]
    assert versions == sorted(versions) and versions[0] == 1
    assert [v for v, _, _ in migrations("sqlite")] == [v for v, _, _ in migrations("postgres")]
    assert cat.migrate() == versions[-1] and cat.schema_version() == versions[-1]


def test_concurrent_commits_of_one_document_serialise(cat):
    """Two writers flip one document at once: exactly one version ends up active,
    the other superseded, and no rows are lost or doubled."""
    trees = [_version(f"ver_{i}", [f"text {i}"]) for i in range(2)]
    errors = []

    def commit(i):
        tree, version, chunks, occ = trees[i]
        try:
            cat.commit_version(DOC, version, tree.elements, tree.pages, chunks, occ)
        except Exception as exc:  # noqa: BLE001 — collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=commit, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    statuses = sorted(v.status for v in cat.list_versions("doc_1"))
    assert statuses == ["committed", "superseded"]
    active = cat.get_document("doc_1").active_version_id
    assert [v.status for v in cat.list_versions("doc_1") if v.id == active] == ["committed"]


def test_commit_flips_and_supersedes_and_diffs(cat):
    first, tree = _commit(cat, "ver_1", ["alpha", "beta"])
    assert first.committed and first.previous_version_id is None and len(first.added_chunk_ids) == 2
    assert cat.get_document("doc_1").active_version_id == "ver_1"
    assert [e.text for e in cat.elements("ver_1", tree.canonical)] == [
        e.text for e in tree.elements
    ]
    second, _ = _commit(cat, "ver_2", ["alpha", "gamma"])
    assert second.previous_version_id == "ver_1"
    assert len(second.removed_chunk_ids) == 1 and len(second.added_chunk_ids) == 1
    assert [v.status for v in cat.list_versions("doc_1")] == ["superseded", "committed"]
    assert [v.ordinal for v in cat.list_versions("doc_1")] == [1, 2]
    assert len(cat.active_chunk_ids(collection_id="col")) == 2


def test_commit_is_idempotent_and_can_return_to_an_old_version(cat):
    _commit(cat, "ver_1", ["alpha"])
    again, _ = _commit(cat, "ver_1", ["alpha"])
    assert not again.committed
    _commit(cat, "ver_2", ["beta"])
    back, _ = _commit(cat, "ver_1", ["alpha"])
    assert back.committed and cat.get_document("doc_1").active_version_id == "ver_1"


def test_commit_needs_the_collection(cat):
    tree, version, chunks, occ = _version("ver_x", ["a"])
    with pytest.raises(CatalogError, match="does not exist"):
        cat.commit_version(
            DOC.model_copy(update={"collection_id": "nope"}),
            version,
            tree.elements,
            [],
            chunks,
            occ,
        )


def test_tombstone_then_purge(cat):
    _commit(cat, "ver_1", ["alpha", "beta"])
    gone = cat.tombstone("doc_1")
    assert len(gone) == 2 and cat.get_document("doc_1").deleted_at is not None
    assert cat.active_chunk_ids(collection_id="col") == set() and cat.list_documents("col") == []
    result = cat.purge("doc_1")
    assert len(result.chunk_ids) == 2 and set(result.orphan_blobs) == {
        "rver_1",
        _version("ver_1", ["alpha", "beta"])[1].text_sha,
    }
    assert cat.get_document("doc_1") is None and cat.blob_refs() == set()


def test_embedding_cache_round_trips_float32(cat):
    cat.put_embeddings("fp", {"a": [0.5, -1.25], "b": [1.0, 2.0]})
    assert cat.get_embeddings("fp", ["a", "b", "c"]) == {"a": [0.5, -1.25], "b": [1.0, 2.0]}
    assert cat.get_embeddings("other", ["a"]) == {}


def test_index_ledger_records_forgets_and_refuses_key_collisions(cat):
    cat.record_index_entries(
        "vector_store:kb", "", "col", [("ch_a", 1, "doc_1"), ("ch_b", 2, "doc_1")]
    )
    cat.record_index_entries("vector_store:kb", "", "col", [("ch_a", 1, "doc_1")])  # idempotent
    assert cat.index_entries("vector_store:kb", "", document_id="doc_1") == {"ch_a": 1, "ch_b": 2}
    with pytest.raises(CatalogError, match="same vector key"):
        cat.record_index_entries("vector_store:kb", "", "col", [("ch_c", 2, "doc_1")])
    assert cat.forget_index_entries("vector_store:kb", "", ["ch_a", "ch_zz"]) == 1
    assert cat.index_entries("vector_store:kb", "", collection_id="col") == {"ch_b": 2}


def test_ingest_log(cat):
    cat.log_ingest("col", "k", "new", document_id="doc_1", stats={"chunks": 2})
    cat.log_ingest("col", "k", "failed", error="boom")
    log = cat.ingest_log("col", "k")
    assert [r["action"] for r in log] == ["new", "failed"] and log[0]["stats"] == {"chunks": 2}


def test_document_acl_round_trips(cat):
    doc = DOC.model_copy(update={"acl": ["team:hr", "user:7"], "tags": ["policy"]})
    tree, version, chunks, occ = _version("ver_1", ["alpha"])
    cat.commit_version(doc, version, tree.elements, tree.pages, chunks, occ)
    got = cat.get_document("doc_1")
    assert got.acl == ["team:hr", "user:7"] and got.tags == ["policy"]


def test_keys_resolve_to_chunks_of_one_store_and_collection_only(cat):
    cat.record_index_entries(
        "vector_store:kb", "", "col", [("ch_a", 1, "doc_1"), ("ch_b", 2, "doc_1")]
    )
    cat.record_index_entries("vector_store:kb", "", "other", [("ch_c", 3, "doc_9")])
    cat.record_index_entries("kb_lexical:main", "", "col", [("ch_a", 1, "doc_1")])
    assert cat.chunks_for_keys("vector_store:kb", "", "col", [1, 2, 3, 4]) == {1: "ch_a", 2: "ch_b"}
    assert cat.chunks_for_keys("kb_lexical:main", "", "col", [1, 2]) == {1: "ch_a"}
    assert cat.chunks_for_keys("vector_store:kb", "", "col", []) == {}


def test_active_chunks_is_the_hydration_gate(cat):
    _, tree = _commit(cat, "ver_1", ["alpha", "beta"])
    v1 = {o.chunk_id for o in cat.version_chunks("ver_1")}
    _commit(cat, "ver_2", ["alpha", "gamma"])
    v2 = {o.chunk_id for o in cat.version_chunks("ver_2")}
    beta = (v1 - v2).pop()
    asked = sorted(v1 | v2) + ["ch_missing"]
    got = cat.active_chunks("col", asked)
    assert set(got) == v2  # the superseded chunk and the unknown id are gone
    hit = got[sorted(v2)[0]]
    assert hit.document.id == "doc_1" and hit.occurrence.version_id == "ver_2"
    assert hit.chunk.text and hit.occurrence.spans
    assert cat.active_chunks("other", asked) == {}  # another collection sees nothing
    assert beta not in got
    cat.tombstone("doc_1")
    assert cat.active_chunks("col", asked) == {}  # a deleted document is invisible at once


def test_update_document_replaces_tags_acl_metadata_without_a_version(cat):
    _commit(cat, "ver_1", ["alpha"])
    assert cat.update_document("doc_1", tags=["a"], acl=["u"], metadata={"x": 1}) is True
    assert cat.update_document("doc_1", tags=["a"], acl=["u"], metadata={"x": 1}) is False
    doc = cat.get_document("doc_1")
    assert (doc.tags, doc.acl, doc.metadata, doc.active_version_id) == (
        ["a"],
        ["u"],
        {"x": 1},
        "ver_1",
    )
    assert len(cat.list_versions("doc_1")) == 1
    with pytest.raises(CatalogError):
        cat.update_document("doc_x", tags=[], acl=[], metadata={})


def test_enrichment_cache_round_trips_answers_and_their_cost(cat):
    from operonx_kb.stores.catalog.base import CachedAnswer

    cat.put_enrichments("fp", "contextual", {
        "a": CachedAnswer(value="Situates the chunk.", model="m", prompt_tokens=120,
                          completion_tokens=12, cached_tokens=64, cost_usd=0.5),
        "b": CachedAnswer(value=[{"title": "Intro", "first_block": 1}]),
    })  # fmt: skip
    got = cat.get_enrichments("fp", ["a", "b", "c"])
    assert set(got) == {"a", "b"}
    assert got["a"] == CachedAnswer(value="Situates the chunk.", model="m", prompt_tokens=120,
                                    completion_tokens=12, cached_tokens=64, cost_usd=0.5)  # fmt: skip
    assert got["b"].value == [{"title": "Intro", "first_block": 1}] and got["b"].cost_usd is None
    cat.put_enrichments("fp", "contextual", {"a": CachedAnswer(value="another answer")})
    assert cat.get_enrichments("fp", ["a"])["a"].value == "Situates the chunk."  # kept
    assert cat.get_enrichments("other", ["a"]) == {}


def test_tree_nodes_commit_with_the_version_and_purge_with_it(cat):
    from operonx_kb.model.tree import TreeNode

    tree, version, chunks, occ = _version("ver_1", ["alpha", "beta"])
    nodes = [
        TreeNode(id="tn_root", version_id="ver_1", path="0", ordinal=0, depth=0, title="Doc",
                 span=(0, len(tree.canonical)), source="document", summary="All of it."),
        TreeNode(id="tn_b", version_id="ver_1", path="0.1", parent_id="tn_root", ordinal=1, depth=1,
                 title="Beta", span=occ[1].spans[0], pages=[2], source="toc", summary_sha="s"),
        TreeNode(id="tn_a", version_id="ver_1", path="0.0", parent_id="tn_root", ordinal=0, depth=1,
                 title="Alpha", span=occ[0].spans[0], source="toc"),
    ]  # fmt: skip
    cat.commit_version(DOC, version, tree.elements, tree.pages, chunks, occ, nodes)
    assert [n.id for n in cat.tree_nodes("ver_1")] == ["tn_root", "tn_a", "tn_b"]
    assert cat.tree_nodes("ver_1")[2] == nodes[1]
    assert cat.tree_nodes("ver_2") == []
    cat.purge("doc_1")
    assert cat.tree_nodes("ver_1") == []
