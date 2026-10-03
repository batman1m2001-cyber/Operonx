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
