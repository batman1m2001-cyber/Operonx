"""The layout scorer and the hand reference it reads."""

import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

from operonx_kb.model.document import Region
from operonx_kb.parsing.base import RawBlock
from operonx_kb.testing.layout_eval import (
    load_reference,
    page_blocks,
    score_layout,
    truth_from_docling,
)

REFERENCE = Path(__file__).parents[1] / "layout_reference" / "reference.json"
needs_pdf = pytest.mark.skipif(
    importlib.util.find_spec("docling_parse") is None, reason="needs the 'pdf' extra"
)
KINDS = {
    "title", "heading", "paragraph", "list_item", "caption", "footnote", "page_header",
    "page_footer", "code", "formula", "table", "figure",
}  # fmt: skip


def _block(kind, text="", page=1, bbox=(0.1, 0.1, 0.2, 0.2), rows=None):
    attrs = {"rows": rows} if rows is not None else {}
    return RawBlock(kind=kind, text=text, regions=[Region(page_no=page, bbox=bbox)], attrs=attrs)


def test_figures_match_by_geometry_and_stay_out_of_text_recall():
    truth = [
        {"kind": "paragraph", "text": "Some body text."},
        {"kind": "figure", "page": 1, "bbox": [0.5, 0.5, 0.9, 0.9]},
    ]
    blocks = [
        _block("paragraph", "Some body text."),
        _block("figure", bbox=(0.55, 0.55, 0.85, 0.7)),  # inside the reference figure
        _block("figure", bbox=(0.05, 0.05, 0.3, 0.3)),  # elsewhere: spurious
    ]
    s = score_layout(truth, blocks)
    assert (s["truth_blocks"], s["text_recall"], s["figures_found"], s["spurious"]) == (
        1,
        1.0,
        1,
        1,
    )


def test_equal_texts_go_to_the_block_of_the_same_kind():
    truth = [{"kind": "caption", "text": "Ho Hoan Kiem"}, {"kind": "title", "text": "Ho Hoan Kiem"}]
    blocks = [_block("title", "Ho Hoan Kiem"), _block("caption", "Ho Hoan Kiem")]
    s = score_layout(truth, blocks)
    assert s["matches"] == [1, 0] and s["kind_accuracy"] == 1.0


def test_page_blocks_keep_blocks_that_start_on_the_page_inside_the_scope():
    b1 = _block("paragraph", "a", page=1, bbox=(0.1, 0.1, 0.3, 0.2))
    b2 = _block("paragraph", "b", page=1, bbox=(0.6, 0.1, 0.9, 0.2))
    b3 = RawBlock(
        kind="paragraph",
        text="continued",
        regions=[
            Region(page_no=1, bbox=(0.1, 0.9, 0.3, 0.95)),
            Region(page_no=2, bbox=(0.1, 0.1, 0.3, 0.2)),
        ],
    )
    assert page_blocks([b1, b2, b3], 1, scope=(0.0, 0.0, 0.5, 1.0)) == [b1, b3]
    assert page_blocks([b1, b2, b3], 2) == []


def test_docling_pictures_become_figures_with_page_boxes():
    doc = {
        "pages": {"1": {"size": {"width": 100.0, "height": 200.0}}},
        "body": {"children": [{"$ref": "#/pictures/0"}, {"$ref": "#/texts/0"}]},
        "texts": [{"self_ref": "#/texts/0", "label": "text", "text": "Body.", "prov": []}],
        "pictures": [
            {
                "self_ref": "#/pictures/0",
                "captions": [],
                "children": [],
                "prov": [
                    {
                        "page_no": 1,
                        "bbox": {
                            "l": 10,
                            "t": 190,
                            "r": 50,
                            "b": 150,
                            "coord_origin": "BOTTOMLEFT",
                        },
                    }
                ],
            }
        ],
    }
    assert truth_from_docling(doc) == [
        {"kind": "figure", "page": 1, "bbox": [0.1, 0.05, 0.5, 0.25]},
        {"kind": "paragraph", "text": "Body."},
    ]


# ── the hand reference ────────────────────────────────────────────────


def test_hand_reference_is_well_formed_and_pins_its_files():
    pages = load_reference(REFERENCE)
    assert 10 <= len(pages) <= 15
    assert len({p["id"] for p in pages}) == len(pages)
    for page in pages:
        assert page["license"] and page["origin"] and page["genre"]
        assert {b["kind"] for b in page["blocks"]} <= KINDS
        for b in page["blocks"]:
            assert ("rows" in b) == (b["kind"] == "table")
            assert ("bbox" in b) == (b["kind"] == "figure")
            if b["kind"] == "heading":
                assert b["level"] >= 1
        if page["path"] is not None:  # committed with the reference
            assert hashlib.sha256(page["path"].read_bytes()).hexdigest() == page["sha256"]


@needs_pdf
def test_hand_reference_texts_come_from_the_text_layer():
    """Every word of every committed reference block is on its page."""
    from operonx_kb.pdf.backend import DoclingParseBackend
    from operonx_kb.text.normalize import normalize_inline

    def words(text):
        return re.findall(r"\w+", normalize_inline(text).lower())

    for page in load_reference(REFERENCE):
        if page["path"] is None:
            continue
        pdf = DoclingParseBackend().pages(page["path"].read_bytes())[page["page"] - 1]
        on_page = set(words(" ".join(w.text for w in pdf.words)))
        for b in page["blocks"]:
            text = b.get("text") or " ".join(" ".join(r) for r in b.get("rows", []))
            missing = [w for w in words(text) if w not in on_page]
            assert not missing, (page["id"], b["kind"], missing)
