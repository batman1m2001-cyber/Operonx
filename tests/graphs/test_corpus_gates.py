"""K1 gates at scale: a generated 200-document corpus and a 100-page PDF.

(a) re-ingest of the unchanged corpus: 0 parse spans, 0 embed calls, 0 upserts;
(b) a one-paragraph edit in a 100-page document re-embeds at most 3 chunks;
(c) purge leaves 0 vectors, 0 ledger rows, 0 orphan blobs;
(d) rebuild matches the original id set and the top-10 results of 50 queries.
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from operonx_kb.model.ids import document_id, vector_id

GOLDEN = Path(__file__).parents[1] / "golden"
HAS_PDF_TOOLS = (
    importlib.util.find_spec("docling_parse") is not None
    and importlib.util.find_spec("reportlab") is not None
    and Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf").exists()
)
pytestmark = [
    pytest.mark.corpus,
    pytest.mark.skipif(not HAS_PDF_TOOLS, reason="needs the pdf extra, reportlab and DejaVu fonts"),
]


def _gen():
    spec = importlib.util.spec_from_file_location("make_corpus", GOLDEN / "make_corpus.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(coro):
    return asyncio.run(coro)


async def held(store):
    ids, _, _ = await store.search([1.0] + [0.0] * 31, top_k=100_000)
    return set(ids)


async def top10(store, embedder, queries):
    out = []
    for q in queries:
        ids, scores, _ = await store.search(embedder.vector(q), top_k=10)
        scores = [round(s, 5) for s in scores]
        out.append((scores, {i for i, s in zip(ids, scores) if s > scores[-1]}))
    return out


def test_200_document_corpus(kb, hub, tmp_path):
    gen = _gen()
    files = gen.corpus(200)
    assert len(files) == 200 and gen.corpus(200) == files  # reproducible
    folder = tmp_path / "corpus"
    folder.mkdir()
    for name, data in files.items():
        (folder / name).write_bytes(data)
    paths = sorted(folder.iterdir())

    first = [run(kb.add("docs", str(p))) for p in paths]
    assert [r["action"] for r in first] == ["new"] * 200
    report = kb.verify("docs")
    assert report.ok, report.problems[:5]
    assert report.documents == 200
    expected = {vector_id(c) for c in kb.catalog.active_chunk_ids(collection_id="docs")}
    assert run(held(kb.store)) == expected

    # (a) unchanged re-ingest
    parses, calls, upserted = (
        kb.recorder.runs("parsed"),
        kb.embedder.calls,
        len(run(held(kb.store))),
    )
    second = [run(kb.add("docs", str(p))) for p in paths]
    assert {r["action"] for r in second} == {"skip"}
    assert kb.recorder.runs("parsed") == parses and kb.embedder.calls == calls
    assert len(run(held(kb.store))) == upserted

    # (d) rebuild into a new generation
    chunks = kb.catalog.get_chunks(sorted(kb.catalog.active_chunk_ids(collection_id="docs")))
    queries = [
        " ".join(c.text.split()[:10]) for c in list(chunks.values())[:: max(1, len(chunks) // 50)]
    ][:50]
    assert len(queries) == 50
    before = run(top10(kb.store, kb.embedder, queries))
    rebuilt = run(kb.rebuild("docs", store="vector_store:kb2", drop_previous=True))
    assert rebuilt["upserted"] == len(expected) and kb.embedder.calls == calls
    new = hub.get("vector_store:kb2")
    assert run(held(new)) == expected
    assert run(top10(new, kb.embedder, queries)) == before

    # (c) purge
    target = paths[17]
    doc = document_id("docs", str(target))
    version = kb.catalog.get_version(kb.document("docs", str(target)).active_version_id)
    vids = set(kb.catalog.index_entries("vector_store:kb2", "", document_id=doc).values())
    assert vids
    run(kb.delete("docs", str(target), purge=True))
    assert run(held(new)) & vids == set()
    assert kb.catalog.index_entries("vector_store:kb2", "", document_id=doc) == {}
    assert not kb.blobs.exists(version.raw_sha) and not kb.blobs.exists(version.text_sha)
    assert kb.verify("docs").ok


def test_one_paragraph_edit_in_a_100_page_pdf(kb, tmp_path):
    gen = _gen()
    path = tmp_path / "manual.pdf"
    path.write_bytes(gen.long_pdf(100))
    first = run(kb.add("docs", str(path)))
    pages = len(kb.catalog.pages(kb.document("docs", str(path)).active_version_id))
    assert pages >= 100
    texts = len(kb.embedder.texts)
    path.write_bytes(gen.long_pdf(100, edited=40))
    second = run(kb.add("docs", str(path)))
    stats = second["stats"]["chunking"]
    reembedded = len(kb.embedder.texts) - texts
    assert second["action"] == "update"
    assert 1 <= reembedded <= 3, stats
    assert stats["new"] == reembedded and stats["removed"] <= 3
    assert first["stats"]["chunking"]["chunks"] > 100
    assert kb.verify("docs").ok
