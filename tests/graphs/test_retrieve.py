"""Retrievers, fusion, the hydration gate and rerank, run as operonx graphs (PLAN R4/R5).

The fakes keep it offline: HashEmbedder vectors share words, the lexical index is
SQLite FTS5, the reranker scores word overlap.
"""

import asyncio
from pathlib import Path

import pytest

from operonx_kb import QueryError
from operonx_kb.model.ids import document_id, vector_id

DOCS = Path(__file__).parents[1] / "golden" / "docs"


def run(coro):
    return asyncio.run(coro)


POLICY = """# Leave policy

## Annual leave

Every employee has twelve days of annual leave per calendar year, booked in the HR portal.

## Sick leave

Sick leave needs a doctor's note after three consecutive days of absence.
"""
TRAVEL = """# Travel policy

## Taxi

Taxi fares on business trips are refunded up to fifty euros per ride with a receipt.

## Hotels

Hotels are booked through the travel desk at least a week ahead.
"""


@pytest.fixture
def loaded(kbx, tmp_path):
    for name, text, tags, dept in (
        ("policy.md", POLICY, ["hr"], "hr"),
        ("travel.md", TRAVEL, ["finance"], "finance"),
    ):
        (tmp_path / name).write_text(text, encoding="utf-8")
        run(kbx.add("docs", str(tmp_path / name), key=name, tags=tags, metadata={"dept": dept}))
    return kbx


@pytest.mark.parametrize("mode", ["dense", "lexical", "hybrid"])
def test_each_mode_finds_the_passage(loaded, mode):
    out = run(loaded.search("docs", "how many days of annual leave", mode=mode, k=3))
    top = out["hits"][0]
    assert "twelve days of annual leave" in top["text"]
    assert top["key"] == "policy.md" and top["rank"] == 1 and top["version_id"].startswith("ver_")
    assert len(out["hits"]) <= 3


def test_hydrated_text_is_the_canonical_text_at_the_hit_spans(loaded):
    hit = run(loaded.search("docs", "taxi fares refunded", mode="hybrid", k=1))["hits"][0]
    canonical = loaded.canonical_text(hit["version_id"])
    assert "\n\n".join(canonical[s:e] for s, e in hit["spans"]) == hit["text"]
    assert hit["heading_path"][-1] == "Taxi"


def test_hybrid_runs_both_retrievers_and_keeps_both_scores(loaded):
    loaded.recorder.clear()
    out = run(loaded.search("docs", "annual leave days", mode="hybrid", k=5))
    # the dense subgraph's vector search and the lexical subgraph's search both ran
    assert loaded.recorder.runs("found") == 1 and loaded.recorder.runs("matched") == 1
    top = out["hits"][0]
    assert top["retriever"] == "hybrid" and set(top["scores"]) == {"dense", "lexical"}


def test_the_gate_drops_a_superseded_chunk_the_index_still_holds(loaded, tmp_path):
    """A crash between the commit and the index delete leaves the old chunk in the index:
    the search still finds it, the catalog gate drops it."""
    old = run(loaded.search("docs", "doctor note three consecutive days", mode="lexical", k=1))
    stale = old["hits"][0]
    (tmp_path / "policy.md").write_text(
        POLICY.replace("three consecutive days", "two consecutive days"), encoding="utf-8"
    )
    run(loaded.add("docs", str(tmp_path / "policy.md"), key="policy.md", tags=["hr"],
                   metadata={"dept": "hr"}))  # fmt: skip
    # Put the superseded chunk back into both indexes, as if its delete never ran.
    doc = document_id("docs", "policy.md")
    entry = [(stale["chunk_id"], vector_id(stale["chunk_id"]), doc)]
    loaded.catalog.record_index_entries("kb_lexical:main", "", "docs", entry)
    payload = {"kb_collection": "docs", "kb_document": doc, "kb_tags": ["hr"], "kb_acl": [],
               "kb_mime": "text/markdown", "kb_created": 0.0, "kb_f_dept": "hr"}  # fmt: skip
    loaded.lexical.upsert([vector_id(stale["chunk_id"])], [["doctor", "three"]], [payload])
    out = run(loaded.search("docs", "doctor three", mode="lexical", k=5))
    assert stale["chunk_id"] not in {h["chunk_id"] for h in out["hits"]}
    assert out["stats"]["dropped_inactive"] == 1


def test_a_tombstoned_document_is_invisible_before_its_entries_are_deleted(loaded):
    loaded.catalog.tombstone(document_id("docs", "travel.md"))  # the index still holds it
    for mode in ("dense", "lexical", "hybrid"):
        out = run(loaded.search("docs", "taxi fares hotels travel desk", mode=mode, k=5))
        assert {h["key"] for h in out["hits"]} <= {"policy.md"}


@pytest.mark.parametrize("mode", ["dense", "lexical", "hybrid"])
def test_filters_hold_in_every_mode(loaded, mode):
    q = "policy leave taxi hotels days"
    only_hr = run(loaded.search("docs", q, mode=mode, k=10, filter={"tags_any": ["hr"]}))["hits"]
    assert only_hr and {h["key"] for h in only_hr} == {"policy.md"}
    fin = run(loaded.search("docs", q, mode=mode, k=10, filter={"fields": {"dept": "finance"}}))
    assert {h["key"] for h in fin["hits"]} == {"travel.md"}
    none = run(loaded.search("docs", q, mode=mode, k=10, filter={"acl_any": ["user:1"]}))
    assert none["hits"] == []


def test_collections_sharing_an_index_never_see_each_other(loaded, tmp_path):
    """Tenant leak, end to end: two collections in one FAISS index and one FTS table."""
    from operonx_kb import CollectionSpec, DenseIndexSpec
    from operonx_kb.model.collection import LexicalIndexSpec

    loaded.create_collection(
        "other",
        CollectionSpec(dense=DenseIndexSpec(embedder="hash", store="vector_store:kb"),
                       lexical=LexicalIndexSpec()),
    )  # fmt: skip
    (tmp_path / "secret.md").write_text(
        "# Secret\n\nAnnual leave for executives is forty days.", encoding="utf-8"
    )
    run(loaded.add("other", str(tmp_path / "secret.md"), key="secret.md"))
    for mode in ("dense", "lexical", "hybrid"):
        mine = run(loaded.search("docs", "annual leave executives forty days", mode=mode, k=20))
        assert "secret.md" not in {h["key"] for h in mine["hits"]}
        theirs = run(loaded.search("other", "annual leave", mode=mode, k=20))
        assert {h["key"] for h in theirs["hits"]} == {"secret.md"}


def test_a_revoked_acl_is_enforced_at_once_though_the_index_payload_is_stale(loaded, tmp_path):
    """Re-adding the same bytes with a new ACL changes the catalog, not the index payloads:
    the index still answers for the old principal, and the catalog gate drops it."""
    path = tmp_path / "salary.md"
    path.write_text("# Salary bands\n\nSalary bands are reviewed every April.", encoding="utf-8")
    run(loaded.add("docs", str(path), key="salary.md", acl=["team:hr"]))
    flt = {"acl_any": ["team:hr"]}
    seen = run(loaded.search("docs", "salary bands", mode="lexical", k=5, filter=flt))
    assert [h["key"] for h in seen["hits"]] == ["salary.md"]
    again = run(loaded.add("docs", str(path), key="salary.md", acl=["team:exec"]))
    assert again["action"] == "skip"
    assert loaded.document("docs", "salary.md").acl == ["team:exec"]
    out = run(loaded.search("docs", "salary bands", mode="lexical", k=5, filter=flt))
    assert out["hits"] == [] and out["stats"]["dropped_filter"] == 1


def test_rerank_reorders_with_the_reranker(loaded, hub):
    hub.alias("reranking:overlap", "fake_reranking:overlap")
    reranker = hub.get("fake_reranking:overlap")
    out = run(loaded.search("docs", "receipt", mode="dense", k=2, reranker="overlap"))
    assert reranker.calls == 1
    assert "receipt" in out["hits"][0]["text"] and "rerank" in out["hits"][0]["scores"]
    assert len(out["hits"]) <= 2


def test_an_undeclared_filter_field_fails_the_search(loaded):
    with pytest.raises(QueryError, match="not filterable"):
        run(loaded.search("docs", "leave", filter={"fields": {"tenant": "x"}}))


def test_a_mode_without_its_index_is_refused(kb):
    with pytest.raises(QueryError, match="lexical"):
        run(kb.search("docs", "leave", mode="lexical"))


def test_the_search_flow_runs_as_a_job(loaded, tmp_path):
    from operonx.app.jobs import Job

    from operonx_kb.graphs.retrieve import build_search_flow

    flow = build_search_flow(loaded.search_graph("docs", mode="hybrid"))
    got = {}  # items run concurrently: collect by key, not by arrival

    def sink(key, item):
        got[key] = item

    job = Job("search_docs", graph=flow, source=[
        {"id": "q1", "query": "annual leave", "collection": "docs", "k": 2},
        {"id": "q2", "query": "taxi", "collection": "docs", "filter": {"tags_any": ["finance"]}},
    ], sink=sink, key="id", record_dir=str(tmp_path / "jobs"))  # fmt: skip
    record = run(job.run())
    assert record.status == "ok", record
    assert len(got["q1"]["hits"]) <= 2 and got["q2"]["hits"][0]["key"] == "travel.md"
