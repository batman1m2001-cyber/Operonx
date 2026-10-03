"""Heuristic layout regressions, each replayed from a crop of a real page.

The crops (tests/unit/crops, written by scripts/crop_page.py) hold the
backend's words, rules and images for one region; the pages are in the hand
reference (tests/layout_reference) or docling's test set. No PDF is parsed.
"""

from pathlib import Path

from operonx_kb.pdf.layout import HeuristicLayout
from operonx_kb.testing.layout_crops import load_crop

CROPS = Path(__file__).parent / "crops"


def _layout(name):
    return HeuristicLayout().layout([load_crop(CROPS / f"{name}.json")])


def _texts(blocks, kind=None):
    return [b.text for b in blocks if kind is None or b.kind == kind]


def test_rotated_arxiv_stamp_is_a_page_header_and_leaves_the_abstract_whole():
    """2206.01062 p1: the stamp's tall words used to chain the abstract's lines."""
    blocks = _layout("arxiv_stamp")
    assert _texts(blocks, "page_header") == ["arXiv:2206.01062v1 [cs.CV] 2 Jun 2022"]
    abstract = [t for t in _texts(blocks) if t.startswith("Accurate document layout")]
    assert len(abstract) == 1
    assert (
        "highquality PDF document conversion. With the recent availability of public" in abstract[0]
    )
    assert not any("[cs.CV]" in t or "2206.01062v1" in t for t in _texts(blocks, "paragraph"))


def test_form_side_label_is_read_bottom_to_top_and_kept_apart():
    """IRS W-9 p1: the rotated 'Print or type' label beside the form fields."""
    blocks = _layout("w9_side_label")
    assert "Print or type. See Specific Instructions on page 3." in _texts(blocks)
    assert not any("Specific" in t for t in _texts(blocks) if not t.startswith("Print or type"))


def test_a_frame_around_a_code_listing_is_not_a_table():
    """code_and_formula p1: the listing's box is four rules around one column of text."""
    blocks = _layout("code_listing_frame")
    assert [b.kind for b in blocks if b.kind == "table"] == []
    assert any(
        "function add(a, b) {" in t and "console.log(add(3, 5));" in t for t in _texts(blocks)
    )


def test_a_slide_frame_is_not_a_table():
    """NASA slide: the title bar and border rules make a grid around the bullets."""
    blocks = _layout("slide_frame")
    assert [b.kind for b in blocks if b.kind == "table"] == []
    assert "Leads core systems US maintenance inside the ISS" in _texts(blocks, "list_item")


def test_a_ruled_grid_over_page_thumbnails_is_a_figure():
    """2206.01062 p1: Figure 1's panels are drawn pages with miniature text."""
    blocks = _layout("figure_thumbnails")
    assert [b.kind for b in blocks if b.kind == "table"] == []
    figures = [b for b in blocks if b.kind == "figure"]
    assert len(figures) == 1 and figures[0].regions[0][1][2] - figures[0].regions[0][1][0] > 200
    assert not any("OPERATION" in t for t in _texts(blocks))  # thumbnail text stays in the figure


def test_narrow_newspaper_columns_are_prose_not_a_borderless_table():
    """newspaper-00 p1 (left page): 100 pt columns of hyphenated text line up row by row."""
    blocks = _layout("newspaper_columns")
    assert [b.kind for b in blocks if b.kind == "table"] == []


def test_side_by_side_form_header_labels_are_not_a_table():
    """IRS W-9 p1: 'Give form to the / requester. Do not / send to the IRS.' wraps."""
    blocks = _layout("w9_header")
    tables = [str(b.rows) for b in blocks if b.kind == "table"]
    assert not any("Give form to the" in t for t in tables)
