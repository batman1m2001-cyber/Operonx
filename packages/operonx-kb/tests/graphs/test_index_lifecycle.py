"""The lexical index follows the catalog through ingest, edit, delete, GC and rebuild,
exactly like the dense index; both carry the filter payload (PLAN R1, R3)."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from operonx_kb import IngestError
from operonx_kb.model.collection import AnalyzerSpec, LexicalIndexSpec
from operonx_kb.model.ids import document_id, vector_id

DOCS = Path(__file__).parents[1] / "golden" / "docs"
CORPUS = sorted(
    p for p in DOCS.iterdir()
    if p.is_file() and (p.suffix != ".pdf" or importlib.util.find_spec("docling_parse") is not None)
)  # fmt: skip
LEX = "kb_lexical:main"


def run(coro):
    return asyncio.run(coro)


def active_keys(kb):
    return {vector_id(c) for c in kb.catalog.active_chunk_ids(collection_id="docs")}


def test_ingest_writes_both_indexes_and_reingest_writes_nothing(kbx):
    for p in CORPUS:
        run(kbx.add("docs", str(p), tags=["golden"], acl=["team:kb"], metadata={"dept": "it"}))
    assert kbx.lexical.ids() == active_keys(kbx)
    assert set(kbx.catalog.index_entries(LEX, "", collection_id="docs").values()) == active_keys(
        kbx
    )
    report = kbx.verify("docs")
    assert report.ok, report.problems
    assert report.lexical_entries == report.chunks
    before = kbx.recorder.runs("lex")
    assert before == len(CORPUS)
    again = [run(kbx.add("docs", str(p))) for p in CORPUS[:3]]
    assert {r["action"] for r in again} == {"skip"} and kbx.recorder.runs("lex") == before


def _guide(n, edited=-1):
    parts = ["# Policy manual\n"]
    for i in range(n):
        text = f"Section {i} explains rule number {i} in enough words to be its own chunk."
        if i == edited:
            text = text.replace("rule number", "the revised rule number")
        parts.append(f"## Topic {i}\n\n{text}\n")
    return "\n".join(parts)


def test_an_edit_moves_one_lexical_entry(kbx, tmp_path):
    path = tmp_path / "manual.md"
    path.write_text(_guide(30), encoding="utf-8")
    run(kbx.add("docs", str(path)))
    before = kbx.lexical.ids()
    path.write_text(_guide(30, edited=7), encoding="utf-8")
    result = run(kbx.add("docs", str(path)))
    assert result["stats"]["lexical"] == {"written": 1, "deleted": 1}
    after = kbx.lexical.ids()
    assert len(after - before) == 1 and len(before - after) == 1
    assert after == active_keys(kbx) and kbx.verify("docs").ok


def test_delete_and_purge_clear_the_lexical_index(kbx):
    for p in CORPUS[:4]:
        run(kbx.add("docs", str(p)))
    target = str(CORPUS[1])
    doc = document_id("docs", target)
    keys = set(kbx.catalog.index_entries(LEX, "", document_id=doc).values())
    assert keys and keys <= kbx.lexical.ids()
    report = run(kbx.delete("docs", target, purge=True))
    assert report["lexical_deleted"] == len(keys)
    assert kbx.lexical.ids() & keys == set()
    assert kbx.catalog.index_entries(LEX, "", document_id=doc) == {}
    assert kbx.verify("docs").ok


def test_gc_collects_lexical_entries_of_a_crashed_delete(kbx):
    path = str(DOCS / "meeting_notes.txt")
    run(kbx.add("docs", path))
    kbx.catalog.tombstone(document_id("docs", path))  # crashed before its index steps
    assert not kbx.verify("docs").ok
    report = run(kbx.gc("docs"))
    assert report["lexical_deleted"] == 1 and kbx.lexical.ids() == set()
    assert kbx.verify("docs").ok


def test_rebuild_the_lexical_index_with_another_analyzer(kbx):
    for p in CORPUS[:4]:
        run(kbx.add("docs", str(p)))
    keys = kbx.lexical.ids()
    parses = kbx.recorder.runs("parsed")
    target = LexicalIndexSpec(
        collection="folded", analyzer=AnalyzerSpec(kind="vi", fold_diacritics=True)
    )
    report = run(kbx.rebuild_lexical("docs", target, switch=True, drop_previous=True))
    assert report["upserted"] == len(keys) and kbx.recorder.runs("parsed") == parses
    assert kbx.lexical.ids("folded") == keys and kbx.lexical.ids() == set()
    assert kbx.collection("docs").spec.lexical == target
    assert kbx.verify("docs").ok


def test_entries_carry_the_filter_payload(kbx):
    from operonx_kb.model.filter import KBFilter
    from operonx_kb.retrieval.filters import native_filter

    run(kbx.add("docs", str(DOCS / "meeting_notes.txt"), tags=["notes"], metadata={"dept": "hr"}))
    run(kbx.add("docs", str(DOCS / "engineering_guide.md"), tags=["eng"], metadata={"dept": "it"}))
    spec = kbx.collection("docs").spec
    hr = kbx.document("docs", str(DOCS / "meeting_notes.txt")).id
    for flt in (KBFilter(tags_any=["notes"]), KBFilter(fields={"dept": "hr"})):
        native = native_filter(kbx.lexical, flt.checked(spec), "docs")
        ids, _ = kbx.lexical.search(["the", "and", "of", "to", "a"], top_k=1000, filter=native)
        assert ids and set(ids) == set(kbx.catalog.index_entries(LEX, "", document_id=hr).values())


def test_a_declared_field_of_the_wrong_type_fails_the_ingest(kbx):
    with pytest.raises(IngestError, match="dept"):
        run(kbx.add("docs", str(DOCS / "meeting_notes.txt"), metadata={"dept": 7}))
    assert kbx.documents("docs") == []


def test_the_passage_template_reaches_the_embedder_and_keys_the_cache(kbx, hub):
    from operonx_kb import CollectionSpec, DenseIndexSpec

    path = str(DOCS / "meeting_notes.txt")
    kbx.create_collection(
        "e5",
        CollectionSpec(dense=DenseIndexSpec(embedder="hash", store="vector_store:kb2",
                                            passage_template="passage: {text}")),
    )  # fmt: skip
    run(kbx.add("e5", path))
    assert kbx.embedder.texts and all(t.startswith("passage: ") for t in kbx.embedder.texts)
    n = len(kbx.embedder.texts)
    run(kbx.add("docs", path))  # same chunks, no template: a different cache key
    assert len(kbx.embedder.texts) > n and not kbx.embedder.texts[-1].startswith("passage: ")
