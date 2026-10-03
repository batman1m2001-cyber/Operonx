import importlib.util
from pathlib import Path

import pytest

from operonx_kb.pdf.assemble import (
    continues,
    fix_ligatures,
    is_caption,
    join_continued,
    join_lines,
    split_list_marker,
)
from operonx_kb.pdf.backend import PdfPage, Rule, Word, font_style
from operonx_kb.pdf.layout import HeuristicLayout

DOCS = Path(__file__).parents[1] / "golden" / "docs"
needs_pdf = pytest.mark.skipif(
    importlib.util.find_spec("docling_parse") is None, reason="needs the 'pdf' extra"
)


# ── assembly rules (docling's) ─────────────────────────────────────────


def test_join_lines_dehyphenates_attached_hyphens_only():
    assert join_lines(["infor-", "mation flows"]) == "information flows"
    assert join_lines(["state -", "of the art"]) == "state - of the art"
    assert (
        join_lines(["snake_case-", "next"]) == "snake_case-next"
    )  # attached, non-alnum word: hyphen kept
    assert join_lines(["one", " ", "two"]) == "one two"


def test_ligatures_expand_and_close_their_gap():
    assert fix_ligatures("ﬁ eld ﬂow") == "field flow"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("• item", ("•", False, "item")),
        ("- item", ("-", False, "item")),
        ("1. item", ("1.", True, "item")),
        ("2.3 item", ("2.3", True, "item")),
        ("(iv) item", ("(iv)", True, "item")),
        ("a) item", ("a)", True, "item")),
        ("plain text", None),
        ("3.5% growth", None),
    ],
)
def test_split_list_marker(text, expected):
    assert split_list_marker(text) == expected


def test_captions_and_continuations():
    assert (
        is_caption("Table 1: Leave") and is_caption("Bảng 2. Phụ cấp") and is_caption("Fig. 3 - x")
    )
    assert not is_caption("Tables are useful")
    assert continues("the sentence goes on,", "and ends here.")
    assert not continues("A finished sentence.", "Next one.")
    assert join_continued("infor-", "mation") == "information"
    assert join_continued("a,", "b") == "a, b"


def test_font_style_from_pdf_font_names():
    assert font_style("/AAAAAA+DejaVuSans-Bold") == (True, False, False)
    assert font_style("/Arial-BoldItalicMT") == (True, True, False)
    assert font_style("CourierNewPSMT") == (False, False, True)
    assert font_style("/F2") == (False, False, False)


# ── heuristic layout on synthetic pages ───────────────────────────────


def _words(text, x, y, size=10.0, bold=False):
    out, cx = [], x
    for w in text.split():
        width = 0.5 * size * len(w)
        out.append(Word(w, cx, y, cx + width, y + size, "Bold" if bold else "Regular", size, bold))
        cx += width + 0.3 * size
    return out


def test_ruled_grid_becomes_a_table():
    page = PdfPage(1, 595, 842)
    for y in (100, 120, 140):
        page.rules.append(Rule(50, y, 250, y))
    for x in (50, 150, 250):
        page.rules.append(Rule(x, 100, x, 140))
    page.words = (
        _words("Name", 55, 105)
        + _words("Days", 155, 105)
        + _words("Annual", 55, 125)
        + _words("12", 155, 125)
    )
    blocks = HeuristicLayout().layout([page])
    assert [(b.kind, b.rows) for b in blocks] == [("table", [["Name", "Days"], ["Annual", "12"]])]


def test_repeated_margin_text_is_furniture_and_page_numbers_too():
    pages = []
    for n in (1, 2):
        page = PdfPage(n, 595, 842)
        page.words = (
            _words("ACME Confidential", 50, 20)
            + _words("Body text here.", 50, 400)
            + _words(str(n), 290, 820)
        )
        pages.append(page)
    blocks = HeuristicLayout().layout(pages)
    assert [b.kind for b in blocks if b.page_no == 1] == ["page_header", "page_footer", "paragraph"]


# ── golden PDFs ───────────────────────────────────────────────────────


@needs_pdf
def test_two_column_report_reading_order_furniture_and_merges():
    from operonx_kb.pdf.parser import PdfParser

    doc = PdfParser().parse((DOCS / "two_column_report.pdf").read_bytes())
    body = [b for b in doc.blocks if b.kind not in ("page_header", "page_footer")]
    assert body[0].kind == "title" and body[0].text == "Structured Chunking for Enterprise Search"
    headings = [(b.text, b.level) for b in body if b.kind == "heading"]
    assert headings == [
        ("1 Introduction", 1),
        ("2 Method", 1),
        ("2.1 Layout analysis", 2),
        ("2.2 Chunking", 2),
        ("3 Results", 1),
    ]
    assert {b.text for b in doc.blocks if b.kind == "page_header"} == {
        "ACME Research - Annual Report 2026"
    }
    assert [b.text for b in doc.blocks if b.kind == "page_footer"] == ["Page 1", "Page 2"]
    items = [b.text for b in body if b.kind == "list_item"]
    assert items[1] == "unchanged paragraphs are never embedded twice;"  # wrapped line joined
    # A paragraph continued across the column break and across the page break.
    merged = [b for b in body if len({(r.page_no, r.bbox[0] > 0.5) for r in b.regions}) > 1]
    assert len(merged) >= 2 and any({r.page_no for r in b.regions} == {1, 2} for b in merged)
    assert body[-1].kind == "footnote"
    assert all(0 <= v <= 1 for b in doc.blocks for r in b.regions for v in r.bbox)


@needs_pdf
def test_table_report_ruled_and_borderless_tables_with_caption():
    from operonx_kb.pdf.parser import PdfParser

    doc = PdfParser().parse((DOCS / "table_report.pdf").read_bytes())
    kinds = [b.kind for b in doc.blocks]
    assert kinds == ["title", "paragraph", "caption", "table", "heading", "paragraph", "table"]
    ruled, borderless = [b.attrs["rows"] for b in doc.blocks if b.kind == "table"]
    assert ruled[0] == ["Contract", "Annual leave", "Sick leave", "Notes"]
    assert ruled[3] == ["Intern", "6 days", "10 days", ""]
    assert borderless == [["Item", "Amount", "Frequency"], ["Lunch", "730,000", "monthly"],
                          ["Parking", "150,000", "monthly"], ["Phone", "200,000", "monthly"]]  # fmt: skip
    assert "accordingly to the consumer price index" in doc.blocks[5].text  # de-hyphenated


@needs_pdf
def test_vietnamese_pdf_text_and_structure():
    from operonx_kb.pdf.parser import PdfParser

    doc = PdfParser().parse((DOCS / "chinh_sach_vi.pdf").read_bytes())
    assert doc.blocks[0].kind == "title" and doc.blocks[0].text == "Chính sách nghỉ phép năm 2026"
    assert [b.text for b in doc.blocks if b.kind == "heading"] == [
        "1. Phạm vi áp dụng",
        "2. Số ngày nghỉ phép",
        "3. Nghỉ ốm",
    ]
    assert [b.attrs.get("marker") for b in doc.blocks if b.kind == "list_item"] == [
        "1.",
        "2.",
        "3.",
    ]
    assert doc.blocks[-1].text.endswith("Trường hợp đặc biệt, giám đốc nhân sự quyết định.")


@needs_pdf
def test_not_a_pdf_is_a_parse_error():
    from operonx_kb.errors import DocumentParseError
    from operonx_kb.pdf.parser import PdfParser

    with pytest.raises(DocumentParseError):
        PdfParser().parse(b"%PDF-1.4 garbage that is not a pdf")


def test_a_marker_like_start_after_a_full_line_is_wrapped_text():
    """'826. For …' after a line that runs to the edge continues the paragraph."""
    page = PdfPage(1, 595, 842)
    page.words = (
        _words("the team keeps reference number one two", 50, 100)
        + _words("826. For release management the", 50, 112)
        + _words("1. a real list item", 50, 140)
    )
    blocks = HeuristicLayout().layout([page])
    assert [(b.kind, b.text) for b in blocks] == [
        ("paragraph", "the team keeps reference number one two 826. For release management the"),
        ("list_item", "a real list item"),
    ]


@needs_pdf
def test_backend_reports_the_text_direction():
    from operonx_kb.pdf.backend import DoclingParseBackend

    pdf = Path(__file__).parents[1] / "layout_reference" / "pdfs" / "irs_fw9_p1.pdf"
    words = {w.text: w for w in DoclingParseBackend().pages(pdf.read_bytes())[0].words}
    assert words["Specific"].angle == 90.0 and not words["Specific"].horizontal
    assert words["Request"].angle == 0.0 and words["Request"].horizontal
    assert words["Specific"].size < 10  # the glyph height, not the rotated box's height
