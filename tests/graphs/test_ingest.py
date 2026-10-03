"""K1 gates, measured on the real graphs (Operon runs, traces, the counting embedder, FAISS).

Needs the upstream operonx branch feat/kb-upstream (VectorUpsertOp/VectorDeleteOp,
BaseVectorStore.delete, operonx.core.media_store) until it merges.
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from operonx_kb import IngestError
from operonx_kb.model.ids import document_id, vector_id

DOCS = Path(__file__).parents[1] / "golden" / "docs"
CORPUS = sorted(
    p for p in DOCS.iterdir()
    if p.is_file() and (p.suffix != ".pdf" or importlib.util.find_spec("docling_parse") is not None)
)  # fmt: skip


def run(coro):
    return asyncio.run(coro)


async def ids_in(store) -> set:
    """Every id the FAISS index holds (a flat index returns all of them for a large k)."""
    ids, _, _ = await store.search([1.0] + [0.0] * 31, top_k=100_000)
    return set(ids)


def ingest_all(kb):
    return [run(kb.add("docs", str(p))) for p in CORPUS]


def test_ingest_corpus_then_reingest_unchanged_is_free(kb):
    """Gate (a) + (b): the corpus commits with valid spans; re-ingesting it parses and embeds nothing."""
    first = ingest_all(kb)
    assert [r["action"] for r in first] == ["new"] * len(CORPUS)
    report = kb.verify("docs")
    assert report.ok, report.problems
    assert report.documents == len(CORPUS) and report.chunks == report.index_entries > 0
    assert run(ids_in(kb.store)) == {
        vector_id(c) for c in kb.catalog.active_chunk_ids(collection_id="docs")
    }

    parses, calls, upserts = (
        kb.recorder.runs("parsed"),
        kb.embedder.calls,
        kb.recorder.runs("upsert"),
    )
    assert parses == len(CORPUS) and upserts == len(CORPUS)
    second = ingest_all(kb)
    assert [r["action"] for r in second] == ["skip"] * len(CORPUS)
    assert kb.recorder.runs("parsed") == parses  # 0 parse spans
    assert kb.embedder.calls == calls  # 0 embed calls
    assert kb.recorder.runs("upsert") == upserts  # 0 index writes
    assert kb.recorder.runs("result") == 2 * len(CORPUS)  # the arms merge: one report per run


def _guide(n_sections: int, edited: int = -1) -> str:
    parts = ["# Policy manual\n"]
    for i in range(n_sections):
        text = f"Section {i} explains rule number {i} in enough words to be its own chunk of the manual."
        if i == edited:
            text = text.replace("rule number", "the revised rule number")
        parts.append(f"## Topic {i}\n\n{text}\n")
    return "\n".join(parts)


def test_one_paragraph_edit_reembeds_only_the_changed_chunk(kb, tmp_path):
    """Gate (b): a one-paragraph edit in a long document re-embeds exactly the changed chunk."""
    path = tmp_path / "manual.md"
    path.write_text(_guide(60), encoding="utf-8")
    first = run(kb.add("docs", str(path)))
    total = first["stats"]["chunking"]["chunks"]
    assert total >= 60
    calls, texts = kb.embedder.calls, len(kb.embedder.texts)
    path.write_text(_guide(60, edited=17), encoding="utf-8")
    second = run(kb.add("docs", str(path)))
    assert second["action"] == "update"
    assert second["stats"]["chunking"] == {
        "chunks": total,
        "new": 1,
        "reused": total - 1,
        "removed": 1,
    }
    assert len(kb.embedder.texts) - texts == 1 and kb.embedder.calls == calls + 1
    assert "revised" in kb.embedder.texts[-1]
    assert second["stats"]["gc_deleted"] == 1  # the old chunk's vector left the index
    assert kb.verify("docs").ok
    assert run(ids_in(kb.store)) == {
        vector_id(c) for c in kb.catalog.active_chunk_ids(collection_id="docs")
    }


def test_same_text_new_bytes_is_a_new_version_with_no_index_writes(kb, tmp_path):
    """Re-saved file (CRLF line ends): new raw bytes, same canonical text — nothing is embedded or written."""
    path = tmp_path / "notes.md"
    path.write_bytes(_guide(5).encode())
    run(kb.add("docs", str(path)))
    calls = kb.embedder.calls
    before = run(ids_in(kb.store))
    path.write_bytes(_guide(5).replace("\n", "\r\n").encode())
    result = run(kb.add("docs", str(path)))
    assert result["action"] == "update" and result["stats"]["chunking"]["new"] == 0
    assert kb.embedder.calls == calls  # nothing embedded
    assert result["stats"]["commit"]["indexed"] == 0  # the upsert got an empty batch: no-op
    assert run(ids_in(kb.store)) == before
    assert kb.recorder.runs("commit") == 2
    assert kb.verify("docs").ok


def test_delete_tombstones_then_readd_hits_the_embedding_cache(kb):
    path = DOCS / "engineering_guide.md"
    run(kb.add("docs", str(path)))
    doc = document_id("docs", str(path))
    vids = set(kb.catalog.index_entries("vector_store:kb", "", document_id=doc).values())
    report = run(kb.delete("docs", str(path)))
    assert report["index_deleted"] == len(vids) and report["purged"] is False
    assert kb.documents("docs") == [] and kb.document("docs", str(path)).deleted_at is not None
    assert run(ids_in(kb.store)) & vids == set()
    calls = kb.embedder.calls
    again = run(kb.add("docs", str(path)))
    assert again["action"] == "new" and again["stats"]["embedding"]["cached"] == len(vids)
    assert kb.embedder.calls == calls  # every vector came from the catalog cache
    assert kb.verify("docs").ok


def test_purge_leaves_nothing_behind(kb):
    """Gate (d): purge leaves 0 index entries, 0 catalog rows and 0 orphan blobs for the document."""
    for p in CORPUS:
        run(kb.add("docs", str(p)))
    target = DOCS / "handbook.docx"
    doc = document_id("docs", str(target))
    version = kb.catalog.get_version(kb.document("docs", str(target)).active_version_id)
    vids = set(kb.catalog.index_entries("vector_store:kb", "", document_id=doc).values())
    before = run(ids_in(kb.store))
    report = run(kb.delete("docs", str(target), purge=True))
    assert (
        report["purged"] and report["index_deleted"] == len(vids) and report["blobs_deleted"] == 2
    )
    assert run(ids_in(kb.store)) == before - vids
    assert (
        kb.catalog.get_document(doc) is None
        and kb.catalog.index_entries("vector_store:kb", "", document_id=doc) == {}
    )
    assert not kb.blobs.exists(version.raw_sha) and not kb.blobs.exists(version.text_sha)
    assert kb.verify("docs").ok


def test_gc_removes_vectors_the_ledger_has_but_no_version_holds(kb):
    path = DOCS / "meeting_notes.txt"
    run(kb.add("docs", str(path)))
    chunk = next(iter(kb.catalog.active_chunk_ids(collection_id="docs")))
    kb.catalog.tombstone(
        document_id("docs", str(path))
    )  # a delete that crashed before its index step
    assert not kb.verify("docs").ok
    report = run(kb.gc("docs"))
    assert report == {"stale": 1, "index_deleted": 1, "ledger_forgotten": 1, "blobs_deleted": 0}
    assert vector_id(chunk) not in run(ids_in(kb.store))
    assert kb.verify("docs").ok


def test_blob_gc_removes_bytes_no_version_references(kb, tmp_path):
    bad = tmp_path / "broken.docx"
    bad.write_bytes(b"not a zip, but stored before the parse failed")
    with pytest.raises(IngestError):
        run(kb.add("docs", str(bad)))
    run(kb.add("docs", str(DOCS / "meeting_notes.txt")))
    assert run(kb.gc("docs", blobs=True))["blobs_deleted"] == 0  # inside the grace period
    assert run(kb.gc("docs", blobs=True, blob_grace_seconds=0))["blobs_deleted"] == 1
    assert kb.verify("docs").ok  # the committed document's blobs are untouched


def test_a_failed_parse_is_raised_logged_and_leaves_the_catalog_alone(kb, tmp_path):
    bad = tmp_path / "broken.docx"
    bad.write_bytes(b"this is not a zip")
    with pytest.raises(IngestError, match="not a DOCX"):
        run(kb.add("docs", str(bad)))
    assert kb.documents("docs") == []
    assert [r["action"] for r in kb.catalog.ingest_log("docs", str(bad))] == ["failed"]


def test_unknown_collection_fails_loudly(kb):
    with pytest.raises(Exception, match="no collection"):
        run(kb.add("nope", str(DOCS / "meeting_notes.txt")))


def test_ingest_flow_runs_as_a_job_over_a_directory(kb, tmp_path):
    from operonx.app.jobs import Job
    from operonx.app.jobs.sources import DirSource

    from operonx_kb.graphs import build_ingest_flow

    (tmp_path / "in").mkdir()
    for name in ("meeting_notes.txt", "quy_trinh_vi.html"):
        (tmp_path / "in" / name).write_bytes((DOCS / name).read_bytes())
    flow = build_ingest_flow(kb.collection("docs").spec.dense)
    got = []
    job = Job("ingest_docs", graph=flow, source=DirSource(tmp_path / "in"), sink=got, key="name",
              inputs={"collection": "docs"}, record_dir=str(tmp_path / "jobs"))  # fmt: skip
    record = run(job.run())
    assert record.status == "ok", record
    assert sorted(r["action"] for r in got) == ["new", "new"]
    assert len(kb.documents("docs")) == 2 and kb.verify("docs").ok


async def _top10(store, embedder, queries):
    """Per query: the top-10 scores, and the ids ranked strictly above the 10th score.

    Identical chunk texts (the corpus repeats a paragraph) score the same, and an
    index may order equal scores either way; only the unambiguous part is compared.
    """
    out = []
    for q in queries:
        ids, scores, _ = await store.search(embedder.vector(q), top_k=10)
        scores = [round(s, 5) for s in scores]
        out.append((scores, {i for i, s in zip(ids, scores) if s > scores[-1]}))
    return out


def _queries(kb, n=50):
    chunks = kb.catalog.get_chunks(sorted(kb.catalog.active_chunk_ids(collection_id="docs")))
    texts = [" ".join(c.text.split()[:8]) for c in chunks.values()]
    return [texts[i % len(texts)] + f" {i}" for i in range(n)]


def test_rebuild_into_a_new_generation_matches_ids_and_top10(kb, hub):
    """K1c gate: a rebuild from the catalog alone reproduces the id set and the
    top-10 results of 50 queries, parses nothing and embeds nothing (cache)."""
    for p in CORPUS:
        run(kb.add("docs", str(p)))
    queries = _queries(kb)
    before_ids = run(ids_in(kb.store))
    before_top = run(_top10(kb.store, kb.embedder, queries))
    parses, calls = kb.recorder.runs("parsed"), kb.embedder.calls
    report = run(kb.rebuild("docs", store="vector_store:kb2", drop_previous=True))
    new = hub.get("vector_store:kb2")
    assert report["switched"] and report["upserted"] == len(before_ids) == report["chunks"]
    assert kb.recorder.runs("parsed") == parses and kb.embedder.calls == calls
    assert run(ids_in(new)) == before_ids
    assert run(_top10(new, kb.embedder, queries)) == before_top
    assert report["previous_deleted"] == len(before_ids) and run(ids_in(kb.store)) == set()
    assert kb.collection("docs").spec.dense.store == "vector_store:kb2"
    assert kb.verify("docs").ok
    # The rebuilt index serves ingest, delete and GC as before.
    run(kb.delete("docs", str(DOCS / "meeting_notes.txt"), purge=True))
    assert kb.verify("docs").ok and len(run(ids_in(new))) < len(before_ids)


def test_rebuild_after_losing_the_index_restores_it_in_place(kb, hub, tmp_path):
    from operonx.core.registry import ResourceHub

    for p in CORPUS[:4]:
        run(kb.add("docs", str(p)))
    expected = run(ids_in(kb.store))
    # A new process: the in-memory FAISS index is gone, the catalog is not.
    fresh = ResourceHub.from_yaml(hub.source_path)
    ResourceHub.set_instance(fresh)
    from operonx_kb import KnowledgeBase

    kb2 = KnowledgeBase()
    assert run(ids_in(fresh.get("vector_store:kb"))) == set()
    report = run(kb2.rebuild("docs"))
    assert report["upserted"] == len(expected) and fresh.get("fake_embedding:hash").calls == 0
    assert run(ids_in(fresh.get("vector_store:kb"))) == expected
    assert kb2.verify("docs").ok
