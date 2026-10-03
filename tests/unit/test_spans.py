import pytest

from operonx_kb.errors import SpanInvariantError
from operonx_kb.model.document import Element, Region, VersionChunk
from operonx_kb.model.ids import sha256_text
from operonx_kb.text import spans as S


def _el(id, kind, span, text, parent=None, layer="body", regions=()):
    return Element(
        id=id,
        content_sha="x",
        version_id="v",
        parent_id=parent,
        path=id,
        ordinal=0,
        depth=0,
        kind=kind,
        layer=layer,
        text=text,
        span=span,
        regions=list(regions),
    )


def test_basic_predicates():
    assert S.is_valid((0, 3), 3) and not S.is_valid((2, 1), 3) and not S.is_valid((0, 4), 3)
    assert S.contains((0, 10), (3, 3)) and not S.contains((0, 10), (5, 11))
    assert S.overlaps((0, 5), (4, 6)) and not S.overlaps((0, 5), (5, 6))
    assert S.intersection((0, 5), (3, 9)) == (3, 5) and S.intersection((0, 2), (2, 3)) is None
    assert S.merge_spans([(5, 7), (0, 2), (2, 4), (8, 9)]) == [(0, 4), (5, 7), (8, 9)]
    assert S.merge_spans([(5, 7), (0, 2), (2, 4), (8, 9)], gap=1) == [(0, 9)]
    assert S.join_spans([(5, 7), (1, 2)]) == (1, 7)
    with pytest.raises(ValueError):
        S.join_spans([])


def test_chunk_text_and_find_span():
    text = "alpha beta gamma"
    assert S.chunk_text(text, [(0, 5), (11, 16)]) == "alpha\n\ngamma"
    assert S.find_span(text, "beta") == (6, 10)
    assert S.find_span(text, "delta") is None and S.find_span(text, "") is None


def test_regions_for_span_maps_to_leaf_regions():
    r1 = Region(page_no=1, bbox=(0, 0, 0.5, 0.1))
    r2 = Region(page_no=2, bbox=(0, 0, 0.5, 0.1))
    els = [
        _el("s", "section", (0, 20), "x" * 20, regions=[r1]),
        _el("a", "paragraph", (0, 8), "x" * 8, parent="s", regions=[r1]),
        _el("b", "paragraph", (10, 20), "x" * 10, parent="s", regions=[r2]),
    ]
    assert [e.id for e in S.elements_in_span(els, (9, 12))] == ["b"]
    assert S.regions_for_span(els, (5, 12)) == [r1, r2]


def test_check_elements_accepts_good_and_names_the_bad():
    canonical = "Hello world"
    good = [
        _el("d", "document", (0, 11), canonical),
        _el("p", "paragraph", (6, 11), "world", parent="d"),
    ]
    assert S.check_elements(canonical, good) == 2
    with pytest.raises(SpanInvariantError, match="canonical\\[span\\]"):
        S.check_elements(canonical, [_el("p", "paragraph", (0, 5), "world")])
    with pytest.raises(SpanInvariantError, match="outside"):
        S.check_elements(canonical, [_el("p", "paragraph", (6, 20), "world")])
    with pytest.raises(SpanInvariantError, match="furniture"):
        S.check_elements(canonical, [_el("f", "page_footer", (0, 1), "H", layer="furniture")])
    with pytest.raises(SpanInvariantError, match="parent"):
        S.check_elements(
            canonical,
            [
                _el("d", "document", (0, 5), "Hello"),
                _el("p", "paragraph", (6, 11), "world", parent="d"),
            ],
        )


def test_check_chunks():
    canonical = "alpha beta gamma"
    occ = VersionChunk(
        version_id="v", chunk_id="c", ordinal=0, spans=[(0, 5), (11, 16)], element_ids=[]
    )
    good = {"c": sha256_text("alpha\n\ngamma")}
    assert S.check_chunks(canonical, [occ], good) == 1
    with pytest.raises(SpanInvariantError, match="content_sha"):
        S.check_chunks(canonical, [occ], {"c": "wrong"})
    bad = VersionChunk(version_id="v", chunk_id="c", ordinal=0, spans=[(3, 3)], element_ids=[])
    with pytest.raises(SpanInvariantError, match="empty"):
        S.check_chunks(canonical, [bad], good)
