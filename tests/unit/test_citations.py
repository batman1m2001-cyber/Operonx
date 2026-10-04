"""Context building and citation verification on a real version tree (no graph, no model)."""

import pytest

from operonx_kb.chunking import StructuralChunker, materialize
from operonx_kb.parsing.markdown import MarkdownParser
from operonx_kb.retrieval.citations import find_quote, verify_citations
from operonx_kb.retrieval.context import build_sources, render_sources
from operonx_kb.structure.build import build_version

DOC = """# Leave policy

## Annual leave

Every employee has twelve days of annual leave per calendar year.

Unused days expire on 31 March of the next year.

## Sick leave

Sick leave needs a doctor's note after three consecutive days.
"""


@pytest.fixture(scope="module")
def version():
    tree = build_version(MarkdownParser().parse(DOC.encode(), name="leave.md"), "ver_1")
    chunker = StructuralChunker(max_tokens=16, min_tokens=0)
    chunks, occ = materialize(tree, chunker.draft(tree), document_id="doc_1", version_id="ver_1",
                              chunker=chunker)  # fmt: skip
    return tree, {c.id: c for c in chunks}, occ


def hit(occ, chunks, i, rank=1):
    o = occ[i]
    return {"chunk_id": o.chunk_id, "version_id": "ver_1", "document_id": "doc_1",
            "key": "leave.md", "title": "Leave policy", "rank": rank,
            "heading_path": chunks[o.chunk_id].heading_path}  # fmt: skip


def sources_for(version, idx, **kw):
    tree, chunks, occ = version
    return build_sources(
        [hit(occ, chunks, i, r + 1) for r, i in enumerate(idx)],
        {"ver_1": occ},
        {c: ch.heading_path for c, ch in chunks.items()},
        {"ver_1": tree.canonical},
        **kw,
    )


def test_chunks_are_one_paragraph_each_here(version):
    _, chunks, occ = version
    texts = [chunks[o.chunk_id].text for o in occ]
    assert any("twelve days" in t and "31 March" not in t for t in texts)


def test_a_hit_is_widened_by_its_neighbours_in_the_same_section_only(version):
    tree, chunks, occ = version
    i = next(i for i, o in enumerate(occ) if "twelve days" in chunks[o.chunk_id].text)
    (src,) = sources_for(version, [i], neighbours=1)
    assert "twelve days" in src.text and "31 March" in src.text
    assert "doctor" not in src.text  # the next section is not a neighbour
    assert len(src.spans) == 1  # the two paragraphs are one stretch of canonical text
    assert src.text == tree.canonical[src.spans[0][0] : src.spans[0][1]]


def test_hits_that_touch_become_one_source(version):
    _, chunks, occ = version
    a = next(i for i, o in enumerate(occ) if "twelve days" in chunks[o.chunk_id].text)
    b = next(i for i, o in enumerate(occ) if "31 March" in chunks[o.chunk_id].text)
    sources = sources_for(version, [a, b], neighbours=0)
    assert len(sources) == 1 and sources[0].hit_ranks == [1, 2]


def test_the_budget_drops_neighbours_then_sources(version):
    _, chunks, occ = version
    i = next(i for i, o in enumerate(occ) if "twelve days" in chunks[o.chunk_id].text)
    j = next(i for i, o in enumerate(occ) if "doctor" in chunks[o.chunk_id].text)
    tight = sources_for(version, [i, j], neighbours=1, budget_tokens=14)
    assert [s.n for s in tight] == [1] and "31 March" not in tight[0].text
    assert "[1] Leave policy › Leave policy › Annual leave" in render_sources([tight[0].as_dict()])


def test_find_quote_ignores_whitespace_and_returns_the_canonical_span(version):
    tree = version[0]
    span = (0, len(tree.canonical))
    got = find_quote(tree.canonical, [span], "  twelve   days of\nannual leave ")
    assert tree.canonical[got[0] : got[1]] == "twelve days of annual leave"
    assert find_quote(tree.canonical, [span], "“twelve days”") is not None
    assert find_quote(tree.canonical, [span], "twelve days of paid leave") is None
    assert find_quote(tree.canonical, [span], "   ") is None


def test_a_quote_outside_the_source_spans_does_not_verify(version):
    tree = version[0]
    at = tree.canonical.index("twelve")
    assert find_quote(tree.canonical, [(0, at)], "twelve days") is None


def test_verify_keeps_verified_drops_the_rest_and_flags_sentences(version):
    tree, chunks, occ = version
    i = next(i for i, o in enumerate(occ) if "twelve days" in chunks[o.chunk_id].text)
    j = next(i for i, o in enumerate(occ) if "doctor" in chunks[o.chunk_id].text)
    sources = [s.as_dict() for s in sources_for(version, [i, j], neighbours=0)]
    answer = "Employees get twelve days [1]. A note is needed after two days [2]. Ask HR [3]."
    out = verify_citations(
        answer,
        [{"source": 1, "quote": "twelve days of annual leave"},
         {"source": 2, "quote": "after two consecutive days"},  # not what the source says
         {"source": 3, "quote": "anything"},  # no such source
         {"quote": "no source number"},
         "not an object"],
        sources, {"ver_1": tree.canonical}, {"ver_1": tree.elements},
    )  # fmt: skip
    assert [c["source"] for c in out["citations"]] == [1]
    cite = out["citations"][0]
    assert tree.canonical[cite["span"][0] : cite["span"][1]] == cite["quote"]
    assert cite["element_ids"] and cite["support"] == "verified"
    assert [d["reason"] for d in out["dropped"]] == [
        "the quote is not in source [2]",
        "there is no source [3]",
        "source is not a number",
        "not a {source, quote} object",
    ]
    assert out["text"] == "Employees get twelve days [1]. A note is needed after two days. Ask HR."
    assert out["unsupported_sentences"] == [1, 2]
    assert out["stats"]["precision"] == 0.2


def test_a_source_with_one_good_quote_keeps_its_marker(version):
    tree, chunks, occ = version
    i = next(i for i, o in enumerate(occ) if "twelve days" in chunks[o.chunk_id].text)
    sources = [s.as_dict() for s in sources_for(version, [i], neighbours=0)]
    out = verify_citations(
        "Twelve days [1, 1].",
        [{"source": 1, "quote": "twelve days"}, {"source": 1, "quote": "fifteen days"}],
        sources, {"ver_1": tree.canonical}, {"ver_1": tree.elements},
    )  # fmt: skip
    assert out["text"] == "Twelve days [1]." and out["unsupported_sentences"] == []
    assert out["stats"]["verified"] == 1 and out["stats"]["dropped"] == 1


def test_a_marker_after_the_full_stop_belongs_to_the_sentence_before():
    from operonx_kb.retrieval.citations import answer_sentences

    text = "Twelve days a year. [1] Unused days expire [2]. Ask HR."
    got = [text[s:e] for s, e in answer_sentences(text)]
    assert got == ["Twelve days a year. [1]", "Unused days expire [2].", "Ask HR."]


def test_a_touching_hit_that_does_not_fit_is_not_claimed_by_the_source(version):
    from operonx_kb.text.tokenize import RegexTokenizer

    _, chunks, occ = version
    a = next(i for i, o in enumerate(occ) if "twelve days" in chunks[o.chunk_id].text)
    b = next(i for i, o in enumerate(occ) if "31 March" in chunks[o.chunk_id].text)
    budget = RegexTokenizer().count(chunks[occ[a].chunk_id].text)
    (src,) = sources_for(version, [a, b], neighbours=0, budget_tokens=budget)
    assert src.hit_ranks == [1] and "31 March" not in src.text


def test_a_quote_is_cleaned_like_the_canonical_text():
    canonical = "Unused days expire on 31 March."
    assert find_quote(canonical, [(0, len(canonical))], "expire on 31​ March") == (12, 30)
