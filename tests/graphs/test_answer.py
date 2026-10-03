"""The answer graph with a scripted model: verified citations resolve to page + bbox,
fabricated ones are dropped and reported (PLAN R6)."""

import asyncio
import importlib.util
import json
import re
from pathlib import Path

import pytest

from operonx_kb import QueryError

DOCS = Path(__file__).parents[1] / "golden" / "docs"
HAS_PDF = importlib.util.find_spec("docling_parse") is not None


def run(coro):
    return asyncio.run(coro)


def sources_of(messages):
    """``n -> text`` of the sources in the answer prompt."""
    user = messages[-1]["content"]
    out = {}
    for block in re.split(
        r"\n\n(?=\[\d+\] )", user.split("Sources:\n\n", 1)[1].split("\n\nQuestion:")[0]
    ):
        head, _, text = block.partition("\n")
        out[int(head[1 : head.index("]")])] = text
    return out


def quoting(fabricate=False):
    """A scripted model that cites the first sentence of source 1 (and, if asked, a quote
    that appears in no source)."""

    def script(messages):
        first = re.split(r"(?<=[.!?])\s", sources_of(messages)[1].strip())[0]
        citations = [{"source": 1, "quote": first}]
        text = f"{first} [1]."
        if fabricate:
            citations.append({"source": 2, "quote": "an invented sentence nobody wrote"})
            text += " It also says something else [2]."
        return json.dumps({"answer": text, "citations": citations})

    return script


@pytest.fixture
def llm(hub):
    hub.alias("llm:answerer", "fake_llm:scripted")
    return hub.get("fake_llm:scripted")


@pytest.fixture
def loaded(kbx):
    for name in ("engineering_guide.md", "meeting_notes.txt", "quy_trinh_vi.html"):
        run(kbx.add("docs", str(DOCS / name), key=name))
    return kbx


def test_a_verified_citation_carries_its_canonical_span(loaded, llm):
    llm.script = quoting()
    answer = run(loaded.ask("docs", "how do we review code", "answerer", mode="hybrid", k=4))
    assert answer["dropped"] == [] and answer["unsupported_sentences"] == []
    (cite,) = answer["citations"]
    canonical = loaded.canonical_text(cite["version_id"])
    assert canonical[cite["span"][0] : cite["span"][1]] == cite["quote"]
    assert cite["element_ids"] and cite["key"] in {s["key"] for s in answer["sources"]}
    assert answer["stats"]["precision"] == 1.0
    prompt = llm.messages[-1]
    assert "Question: how do we review code" in prompt[-1]["content"]
    assert "word for word" in prompt[0]["content"]


def test_a_fabricated_citation_is_dropped_and_its_sentence_flagged(loaded, llm):
    llm.script = quoting(fabricate=True)
    answer = run(loaded.ask("docs", "how do we review code", "answerer", mode="hybrid", k=4))
    assert len(answer["citations"]) == 1 and len(answer["dropped"]) == 1
    assert answer["dropped"][0]["reason"].startswith(
        ("the quote is not in source", "there is no source")
    )
    assert "[2]" not in answer["text"] and answer["unsupported_sentences"] == [1]
    assert answer["stats"]["precision"] == 0.5


def test_a_reply_that_does_not_parse_fails_loudly(loaded, llm):
    llm.script = lambda messages: "I think the answer is twelve."
    with pytest.raises(QueryError, match="not an answer with citations"):
        run(loaded.ask("docs", "anything", "answerer", k=2))


@pytest.mark.skipif(not HAS_PDF, reason="needs the pdf extra")
def test_a_citation_into_a_pdf_resolves_to_page_and_box(kbx, llm):
    run(kbx.add("docs", str(DOCS / "two_column_report.pdf"), key="report.pdf"))
    llm.script = quoting()
    answer = run(kbx.ask("docs", "what does the report conclude", "answerer", mode="lexical", k=3))
    (cite,) = answer["citations"]
    assert cite["pages"] and cite["regions"]
    for region in cite["regions"]:
        x0, y0, x1, y1 = region["bbox"]
        assert region["page_no"] in cite["pages"] and 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1


def test_the_answer_flow_runs_behind_doors(loaded, llm, tmp_path):
    from operonx.app.jobs import Job

    from operonx_kb.graphs.answer import build_answer_flow

    llm.script = quoting()
    flow = build_answer_flow(loaded.answer_graph("docs", "answerer", mode="dense"))
    got = []
    job = Job("ask_docs", graph=flow, source=[{"id": "a", "query": "meeting", "collection": "docs"}],
              sink=got, key="id", record_dir=str(tmp_path / "jobs"))  # fmt: skip
    record = run(job.run())
    assert record.status == "ok", record
    assert got[0]["citations"] and got[0]["citations"][0]["support"] == "verified"
