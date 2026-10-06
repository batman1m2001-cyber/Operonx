"""Property tests: the span invariant and id determinism hold for any block list."""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from operonx_kb.parsing.base import ParsedDoc, RawBlock
from operonx_kb.structure.build import build_version
from operonx_kb.text.spans import check_elements

_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), max_codepoint=0x2FFF), max_size=40
)
_cell = st.text(alphabet=st.sampled_from(list("ab |\\é́ \t1")), max_size=6)


@st.composite
def blocks(draw):
    kind = draw(
        st.sampled_from(
            [
                "title",
                "heading",
                "paragraph",
                "list_item",
                "table",
                "figure",
                "caption",
                "formula",
                "code",
                "footnote",
                "kv",
                "page_header",
                "page_footer",
            ]
        )
    )
    if kind == "table":
        rows = draw(st.lists(st.lists(_cell, min_size=1, max_size=4), min_size=1, max_size=4))
        return RawBlock(kind="table", attrs={"rows": rows})
    return RawBlock(
        kind=kind,
        text=draw(_text),
        level=draw(st.integers(min_value=-1, max_value=9)) if kind == "heading" else None,
        depth=draw(st.integers(min_value=0, max_value=3)) if kind == "list_item" else 0,
        attrs={"ordered": draw(st.booleans())} if kind == "list_item" else {},
    )


@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(blocks(), max_size=25))
def test_any_block_list_satisfies_the_span_invariant(bs):
    tree = build_version(ParsedDoc(blocks=bs), "ver_prop")
    assert check_elements(tree.canonical, tree.elements) == len(tree.elements)
    # Body leaves appear in canonical order, without overlap.
    leaves = [
        e
        for e in tree.elements
        if e.layer == "body" and e.kind not in ("document", "section", "list")
    ]
    for a, b in zip(leaves, leaves[1:]):
        assert a.span[1] <= b.span[0]
    # Ordinals are contiguous per parent; the tree is a tree.
    children = {}
    for e in tree.elements:
        children.setdefault(e.parent_id, []).append(e.ordinal)
    assert all(sorted(v) == list(range(len(v))) for v in children.values())
    assert children[None] == [0]


@settings(max_examples=100)
@given(st.lists(blocks(), max_size=12))
def test_building_twice_gives_identical_ids_and_text(bs):
    a = build_version(ParsedDoc(blocks=bs), "ver_same")
    b = build_version(ParsedDoc(blocks=bs), "ver_same")
    assert a.canonical == b.canonical
    assert [(e.id, e.content_sha, e.span) for e in a.elements] == [
        (e.id, e.content_sha, e.span) for e in b.elements
    ]
