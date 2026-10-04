"""The admin API ``operonx-kb/1`` (track5 §16.2): what Studio's Knowledge tab reads.

Every answer is checked against the catalog it reads (the store of record), and a
citation's boxes against the page they are drawn on.
"""

import asyncio
import importlib.util
import json
import re
import struct
from pathlib import Path

import pytest

pytest.importorskip("starlette")
from starlette.testclient import TestClient

from operonx_kb.admin import API, kb_admin_app
from operonx_kb.model.ids import document_id, vector_id

DOCS = Path(__file__).parents[1] / "golden" / "docs"
HAS_PDF = importlib.util.find_spec("docling_parse") is not None
needs_pdf = pytest.mark.skipif(not HAS_PDF, reason="needs the pdf extra")


def run(coro):
    return asyncio.run(coro)


def first_sentence_of_source_1(messages):
    """A scripted model that cites the first sentence of source 1, verbatim."""
    user = messages[-1]["content"]
    block = user.split("Sources:\n\n", 1)[1].split("\n\nQuestion:")[0]
    text = re.split(r"\n\n(?=\[\d+\] )", block)[0].partition("\n")[2]
    first = re.split(r"(?<=[.!?])\s", text.strip())[0]
    return json.dumps({"answer": f"{first} [1]", "citations": [{"source": 1, "quote": first}]})


@pytest.fixture
def llm(hub):
    hub.alias("llm:answerer", "fake_llm:scripted")
    fake = hub.get("fake_llm:scripted")
    fake.script = first_sentence_of_source_1
    return fake


@pytest.fixture
def loaded(kbx):
    names = ["engineering_guide.md", "meeting_notes.txt", "quy_trinh_vi.html"]
    if HAS_PDF:
        names += ["two_column_report.pdf", "chinh_sach_vi.pdf"]
    for name in names:
        run(kbx.add("docs", str(DOCS / name), key=name))
    return kbx


@pytest.fixture
def client(loaded, llm):
    app = kb_admin_app(llm="answerer", trace=loaded.recorder)
    with TestClient(app) as c:
        c.kb = loaded
        yield c


def ok(res):
    assert res.status_code == 200, res.text
    return res.json()


# ── discovery and collections ────────────────────────────────────────────


def test_discovery_names_the_contract_and_what_a_query_may_ask(client):
    got = ok(client.get("/.well-known/operonx-kb"))
    assert got == {"api": API, "collections": ["docs"], "answer": True, "rerank": False}
    assert API == "operonx-kb/1"


def test_a_collection_shows_its_spec_modes_and_counts(client):
    (c,) = ok(client.get("/collections"))["collections"]
    assert c["id"] == "docs" and c["modes"] == ["dense", "lexical", "hybrid"]
    assert c["default_mode"] == "hybrid" and c["filterable"] == {"dept": "keyword"}
    docs = client.kb.documents("docs")
    assert c["documents"] == c["active"] == len(docs) and c["deleted"] == 0
    assert c["chunks"] == len(client.kb.catalog.active_chunk_ids("docs"))
    assert ok(client.get("/collections/docs")) == c
    res = client.get("/collections/nope")
    assert res.status_code == 404 and "GET /collections" in res.json()["error"]


def test_a_collection_with_a_tree_index_serves_tree_mode(client):
    from operonx_kb import TreeSpec

    spec = client.kb.collection("docs").spec
    client.kb.create_collection("docs", spec.model_copy(update={"tree": TreeSpec(llm="answerer")}))
    got = ok(client.get("/collections/docs"))
    assert (
        got["modes"] == ["dense", "lexical", "hybrid", "tree"] and got["default_mode"] == "hybrid"
    )


def test_health_is_verify(client):
    got = ok(client.get("/collections/docs/health"))
    report = client.kb.verify("docs")
    assert got["ok"] is True and got["problems"] == []
    assert (got["documents"], got["chunks"], got["index_entries"]) == (
        report.documents,
        report.chunks,
        report.index_entries,
    )


def test_documents_page_filter_and_status(client):
    every = ok(client.get("/collections/docs/documents"))
    assert every["total"] == len(client.kb.documents("docs")) and every["page"] == 1
    assert all(d["status"] == "active" and d["stats"]["chunks"] > 0 for d in every["documents"])
    found = ok(client.get("/collections/docs/documents", params={"q": "MEETING"}))
    assert [d["key"] for d in found["documents"]] == ["meeting_notes.txt"]
    paged = ok(client.get("/collections/docs/documents", params={"size": 2, "page": 2}))
    assert [d["key"] for d in paged["documents"]] == [d["key"] for d in every["documents"][2:4]]
    run(client.kb.delete("docs", "meeting_notes.txt"))
    gone = ok(client.get("/collections/docs/documents", params={"status": "deleted"}))
    assert [d["key"] for d in gone["documents"]] == ["meeting_notes.txt"]
    assert gone["documents"][0]["deleted_at"]
    assert ok(client.get("/collections/docs/documents"))["total"] == every["total"] - 1
    assert client.get("/collections/docs/documents", params={"status": "lost"}).status_code == 400
    assert client.get("/collections/docs/documents", params={"size": 0}).status_code == 400


def test_a_document_shows_its_versions_and_ingest_log(client):
    doc_id = document_id("docs", "engineering_guide.md")
    got = ok(client.get(f"/documents/{doc_id}"))
    assert got["key"] == "engineering_guide.md" and got["collection_id"] == "docs"
    (v,) = got["versions"]
    assert v["id"] == got["active_version_id"] and v["status"] == "committed"
    assert [e["action"] for e in got["log"]] == ["new"]
    assert client.get("/documents/doc_missing").status_code == 404


# ── versions, pages, chunks ──────────────────────────────────────────────


def _version(client, key):
    return client.kb.document("docs", key).active_version_id


def test_a_version_lists_its_chunks_with_their_spans(client):
    vid = _version(client, "engineering_guide.md")
    got = ok(client.get(f"/versions/{vid}"))
    text = ok(client.get(f"/versions/{vid}/text"))["text"]
    assert text == client.kb.canonical_text(vid)
    occurrences = client.kb.catalog.version_chunks(vid)
    assert [c["chunk_id"] for c in got["chunks"]] == [o.chunk_id for o in occurrences]
    assert got["pages"] == [] and got["active"] is True
    for c, o in zip(got["chunks"], occurrences):
        assert c["spans"] == [list(s) for s in o.spans] and c["regions"] == []
    tree = ok(client.get(f"/versions/{vid}/tree"))["elements"]
    body = [e for e in tree if e["span"]]
    assert body and all(text[e["span"][0] : e["span"][1]] == e["text"] for e in body)
    assert client.get("/versions/ver_missing").status_code == 404


@needs_pdf
def test_a_pdf_page_lists_the_boxes_of_its_elements_and_chunks(client):
    vid = _version(client, "two_column_report.pdf")
    version = ok(client.get(f"/versions/{vid}"))
    assert [p["page_no"] for p in version["pages"]] == [
        p.page_no for p in client.kb.catalog.pages(vid)
    ]
    got = ok(client.get(f"/versions/{vid}/pages/1"))
    tree = {e.id: e for e in client.kb.catalog.elements(vid, client.kb.canonical_text(vid))}
    assert got["elements"] and got["pages"] == len(version["pages"])
    for e in got["elements"]:
        want = [list(r.bbox) for r in tree[e["id"]].regions if r.page_no == 1]
        assert e["boxes"] == [[round(v, 5) for v in b] for b in want]
    on_page = [c for c in version["chunks"] if 1 in c["pages"]]
    assert [c["chunk_id"] for c in got["chunks"]] == [c["chunk_id"] for c in on_page]
    for c in got["chunks"]:
        x0, y0, x1, y1 = c["boxes"][0]
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    res = client.get(f"/versions/{vid}/pages/99")
    assert res.status_code == 404 and "pages are 1 to" in res.json()["error"]


@needs_pdf
def test_a_page_image_is_the_pdf_page_at_the_scale_asked(client):
    vid = _version(client, "two_column_report.pdf")
    page = client.kb.catalog.pages(vid)[0]
    res = client.get(f"/versions/{vid}/pages/1/image", params={"scale": 2})
    assert res.status_code == 200 and res.headers["content-type"] == "image/png"
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", res.content[16:24])
    assert (width, height) == (round(page.width * 2), round(page.height * 2))
    assert "immutable" in res.headers["cache-control"]
    assert client.get(f"/versions/{vid}/pages/1/image", params={"scale": 9}).status_code == 400
    md = _version(client, "engineering_guide.md")
    res = client.get(f"/versions/{md}/pages/1/image")
    assert res.status_code == 404 and "only PDF pages have images" in res.json()["error"]


def test_the_chunk_inspector_shows_text_context_neighbours_and_indexes(client):
    vid = _version(client, "engineering_guide.md")
    occurrences = client.kb.catalog.version_chunks(vid)
    middle = occurrences[1]
    got = ok(client.get(f"/chunks/{middle.chunk_id}"))
    chunk = client.kb.catalog.get_chunks([middle.chunk_id])[middle.chunk_id]
    assert got["text"] == chunk.text and got["embed_text"] == chunk.embed_text
    assert (
        got["context_prefix"] + chunk.text
        == chunk.embed_text[: len(got["context_prefix"]) + len(chunk.text)]
    )
    assert got["version_id"] == vid and got["ordinal"] == middle.ordinal
    assert got["neighbours"] == {
        "previous": occurrences[0].chunk_id,
        "next": occurrences[2].chunk_id,
    }
    dense, lexical = got["indexes"]
    assert dense["kind"] == "dense" and dense["key"] == vector_id(middle.chunk_id)
    assert lexical["kind"] == "lexical" and lexical["key"] == vector_id(middle.chunk_id)
    assert dense["embedded_as"] == chunk.embed_text  # the collection's template is "{text}"
    assert client.get("/chunks/ch_missing").status_code == 404
    other = _version(client, "meeting_notes.txt")
    res = client.get(f"/chunks/{middle.chunk_id}", params={"version": other})
    assert res.status_code == 404 and "does not hold" in res.json()["error"]


# ── query ────────────────────────────────────────────────────────────────


def test_a_query_lists_each_mode_and_answers_each_run_under_its_trace_id(client):
    client.kb.recorder.clear()
    got = ok(
        client.post(
            "/collections/docs/query",
            json={
                "query": "how do we review code",
                "modes": ["dense", "lexical", "hybrid"],
                "answer": True,
                "k": 4,
            },
        )
    )
    assert [s["mode"] for s in got["searches"]] == ["dense", "lexical", "hybrid"]
    for s in got["searches"]:
        want = run(client.kb.search("docs", "how do we review code", k=4, mode=s["mode"]))
        assert [h["chunk_id"] for h in s["hits"]] == [h["chunk_id"] for h in want["hits"]]
    answer = got["answer"]
    assert answer["mode"] == "hybrid" and answer["citations"] and answer["dropped"] == []
    traced = {t.trace_id for t in client.kb.recorder.traces}
    assert {s["trace_id"] for s in got["searches"]} | {answer["trace_id"]} <= traced
    assert len({s["trace_id"] for s in got["searches"]} | {answer["trace_id"]}) == 4
    ((start, end),) = answer["sentences"]
    assert answer["text"][start:end].endswith("[1]")


@needs_pdf
def test_a_citation_into_a_pdf_names_the_page_and_boxes_the_viewer_draws(client):
    got = ok(
        client.post(
            "/collections/docs/query",
            json={
                "query": "what does the report conclude",
                "modes": ["lexical"],
                "mode": "lexical",
                "answer": True,
                "k": 3,
                "filter": {"document_ids": [document_id("docs", "two_column_report.pdf")]},
            },
        )
    )
    (cite,) = got["answer"]["citations"]
    vid = cite["version_id"]
    canonical = client.kb.canonical_text(vid)
    assert canonical[cite["span"][0] : cite["span"][1]] == cite["quote"]
    first = cite["regions"][0]
    page = ok(client.get(f"/versions/{vid}/pages/{first['page_no']}"))
    drawn = [b for e in page["elements"] if e["id"] in cite["element_ids"] for b in e["boxes"]]
    on_page = [r["bbox"] for r in cite["regions"] if r["page_no"] == first["page_no"]]
    assert on_page and all([round(v, 5) for v in b] in drawn for b in on_page)


def test_a_query_refuses_what_the_collection_or_app_cannot_do(client, loaded):
    assert client.post("/collections/docs/query", json={}).status_code == 400
    res = client.post("/collections/docs/query", json={"query": "x", "modes": ["tree"]})
    assert res.status_code == 400 and "hybrid" in res.json()["error"]
    res = client.post(
        "/collections/docs/query", json={"query": "x", "filter": {"fields": {"room": "a"}}}
    )
    assert res.status_code == 400 and "room" in res.json()["error"]
    res = client.post("/collections/docs/query", json={"query": "x", "rerank": True})
    assert res.status_code == 501 and "reranker=" in res.json()["error"]
    assert client.post("/collections/docs/query", content=b"not json").status_code == 400
    with TestClient(kb_admin_app(trace=loaded.recorder)) as bare:
        assert ok(bare.get("/.well-known/operonx-kb"))["answer"] is False
        res = bare.post("/collections/docs/query", json={"query": "x", "answer": True})
        assert res.status_code == 501 and "llm=" in res.json()["error"]


def test_a_failed_answer_names_the_run_to_open(client, llm):
    llm.script = lambda messages: "not json at all"
    client.kb.recorder.clear()
    res = client.post("/collections/docs/query", json={"query": "anything", "answer": True})
    assert res.status_code == 500
    body = res.json()
    assert "ParserError" in body["error"]
    assert body["trace_id"] in {t.trace_id for t in client.kb.recorder.traces}


# ── eval cases ───────────────────────────────────────────────────────────


def test_an_eval_case_is_a_dataset_row_whose_labels_resolve(client, tmp_path):
    from operonx.app.evals import Dataset

    from operonx_kb.eval import LabelResolver

    got = ok(
        client.post(
            "/collections/docs/query", json={"query": "how do we review code", "answer": True}
        )
    )
    cites = [
        {"key": c["key"], "quote": c["quote"], "pages": c["pages"]}
        for c in got["answer"]["citations"]
    ]
    body = {
        "query": " how do we  review code",
        "citations": cites,
        "answer": "With two reviewers.",
        "k": 5,
    }
    row = ok(client.post("/collections/docs/eval-case", json=body))["row"]
    assert row["input"] == {"query": "how do we  review code", "collection": "docs", "k": 5}
    assert row["expected"]["answer"] == "With two reviewers." and row["tags"] == ["studio"]
    assert row["expected"]["relevant"] == [
        {"doc_key": c["key"], "quote": c["quote"]} for c in cites
    ]
    again = ok(
        client.post("/collections/docs/eval-case", json={**body, "query": "how do we review code"})
    )
    assert again["row"]["id"] == row["id"]  # one question, one case
    resolved = LabelResolver().resolve("docs", row["expected"]["relevant"])
    assert all(resolved)
    path = tmp_path / "cases.jsonl"
    assert Dataset(path).add([row]) and Dataset(path).add([again["row"]]) == []
    stale = {
        "query": "q",
        "citations": [{"key": "engineering_guide.md", "quote": "words nobody wrote"}],
    }
    res = client.post("/collections/docs/eval-case", json=stale)
    assert res.status_code == 400 and "stale or wrong" in res.json()["error"]
    assert (
        client.post("/collections/docs/eval-case", json={"query": "q", "citations": []}).status_code
        == 400
    )


# ── served ───────────────────────────────────────────────────────────────


def test_the_app_answers_under_the_path_operonx_serve_mounts_it_at(loaded, llm):
    """``Service("kb_admin", asgi("/kb", ...), app=kb_admin_app(...))``: operonx mounts it."""
    from operonx.app import Service, asgi
    from operonx.app.serve.app import build_app

    service = Service(
        "kb_admin", asgi("/kb", port=8021), app=kb_admin_app(llm="answerer", trace=loaded.recorder)
    )
    with TestClient(build_app((service,), startup=False)) as served:
        assert ok(served.get("/kb/.well-known/operonx-kb"))["api"] == API
        assert ok(served.get("/kb/collections"))["collections"][0]["id"] == "docs"


def test_an_app_served_before_the_resources_are_loaded_says_how_to_load_them():
    """``operonx serve`` loads no resources for an asgi-only listener: the project does."""
    from operonx.core.registry import ResourceHub

    ResourceHub.reset_instance()
    with TestClient(kb_admin_app()) as bare:
        res = bare.get("/collections")
    assert res.status_code == 501
    assert "operonx.bootstrap()" in res.json()["error"] and "kb_catalog:main" in res.json()["error"]
