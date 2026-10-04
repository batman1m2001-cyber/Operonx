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


def test_newspaper_paragraphs_survive_ink_measured_fonts_and_loose_leading():
    """newspaper-00 p1: word boxes follow the ink (4.5 to 8.3 pt in one face) and
    the lines sit 0.53 box heights apart; both used to cut every paragraph."""
    blocks = _layout("newspaper_columns")
    texts = _texts(blocks, "paragraph")
    first = [t for t in texts if t.startswith("Heute lest ihr")]
    assert len(first) == 1 and first[0].endswith("Danke für eure Treue!")
    assert not [b for b in blocks if b.kind == "heading" and b.text.startswith("gabe der")]


def test_a_justified_line_with_wide_spaces_stays_one_line():
    """elsevier-00 p2: 'strategies  may  significantly  enhance ...' was cut into words."""
    blocks = _layout("justified_wide_spaces")
    para = [t for t in _texts(blocks) if t.startswith("2020). These attributes")]
    assert len(para) == 1
    assert (
        "strategies may significantly enhance both their scientific and socio-economic" in para[0]
    )


def test_paragraphs_of_a_column_and_a_caption_cut_by_a_tab():
    """2203.01017 p4: indented first lines start paragraphs even when the block's
    own first line is indented; 'Table 1:   Both ...' is one caption."""
    blocks = _layout("indent_and_cut_caption")
    texts = _texts(blocks)
    assert any(t.startswith("As it is illustrated in Fig. 2") for t in texts)
    assert any(t.startswith("Motivated by those observations") for t in texts)
    captions = _texts(blocks, "caption")
    assert len(captions) == 1 and captions[0].startswith('Table 1: Both "Combined-Tabnet"')


def test_a_whole_italic_line_does_not_run_into_the_paragraph_below():
    """amt_handbook p1: the italic subheading 'Boots Self-Locking Nut'."""
    blocks = _layout("italic_heading")
    assert "Boots Self-Locking Nut" in _texts(blocks)


def test_side_by_side_labels_of_a_form_header_are_separate_blocks():
    """IRS W-9 p1: the header's three boxes share text lines."""
    blocks = _layout("w9_header")
    assert "Give form to the requester. Do not send to the IRS." in _texts(blocks)
    assert "Under penalties of perjury, I certify that:" in _texts(blocks)


def test_korean_brief_dates_footnotes_and_title():
    """normal_4pages p1: '2020.3.30. 0시 ...' is a date, not a list marker; small
    '1) ...' notes low on the page are footnotes; the 35 pt line below a masthead
    issue number is the title."""
    blocks = _layout("korean_brief")
    assert [b.text for b in blocks if b.kind == "title"] == [
        "코로나-19 관련 보험약관상 재해보험금 지급문제 및 개선과제"
    ]
    date = [b for b in blocks if b.text.startswith("2020.3.30.")]
    assert len(date) == 1 and date[0].kind == "paragraph"
    footnotes = _texts(blocks, "footnote")
    assert any(t.startswith("1) 손해보험의 표준약관") for t in footnotes)
    assert any(t.startswith("* 경제산업조사실") for t in footnotes)


def test_centred_title_lines_are_one_title():
    """2206.01062 p1: the second centred title line starts right of the first;
    that is not a first-line indent."""
    blocks = _layout("centred_title")
    assert _texts(blocks, "title") == [
        "DocLayNet: A Large Human-Annotated Dataset for Document-Layout Analysis"
    ]


def test_a_page_number_in_a_deep_bottom_margin_is_a_footer():
    """code_and_formula p1: the folio sits 12% above the page bottom (LaTeX margins)."""
    blocks = _layout("page_number_low")
    assert _texts(blocks, "page_footer") == ["1"]


def test_bullets_set_apart_from_their_text_start_list_items():
    """NASA slide 11: '•' then a wide gap, then a larger bold label."""
    blocks = _layout("slide_bullets")
    items = _texts(blocks, "list_item")
    assert "Pro 1RF:" in items and "Pro No Oscillation: -provides larger window of" in items
    assert not [t for t in _texts(blocks, "heading") if t.startswith("•")]
