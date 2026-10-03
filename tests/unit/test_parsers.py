import io
import zipfile
from pathlib import Path

import pytest

from operonx_kb.errors import DocumentParseError, UnsupportedFormatError
from operonx_kb.parsing.docx import DocxParser
from operonx_kb.parsing.html import HtmlParser, html_to_blocks
from operonx_kb.parsing.markdown import MarkdownParser, strip_inline
from operonx_kb.parsing.plain import PlainTextParser
from operonx_kb.parsing.pptx import PptxParser
from operonx_kb.parsing.router import ParserRouter, sniff_mime
from operonx_kb.parsing.xlsx import XlsxParser, cell_ref, parse_ref

DOCS = Path(__file__).parents[1] / "golden" / "docs"


def kinds(doc):
    return [(b.kind, b.text) for b in doc.blocks]


# ── plain ─────────────────────────────────────────────────────────────


def test_plain_splits_on_blank_lines_and_handles_bom():
    doc = PlainTextParser().parse("﻿a\nb\n\n  \nc".encode("utf-8"))
    assert kinds(doc) == [("paragraph", "a\nb"), ("paragraph", "c")]


def test_plain_refuses_undecodable_bytes_and_names_the_fix():
    with pytest.raises(DocumentParseError, match="encoding="):
        PlainTextParser().parse("Nghỉ".encode("utf-16-le"))
    assert PlainTextParser(encoding="cp1252").parse(b"caf\xe9").blocks[0].text == "café"


def test_parser_fingerprint_tracks_config():
    assert PlainTextParser().fingerprint() != PlainTextParser(encoding="cp1252").fingerprint()


# ── markdown ─────────────────────────────────────────────────────────


def test_markdown_blocks():
    md = (
        "# Only title\n\nPara **bold** and [link](http://x).\n\n## Sub\n\n- a\n  - b\n- c\n\n"
        "3) three\n\n| h | i |\n|---|---|\n| 1 |\n\n```py\nx = 1\n```\n\nSetext\n---\n"
    )
    doc = MarkdownParser().parse(md.encode())
    got = [(b.kind, b.level, b.depth, b.text) for b in doc.blocks if b.kind != "table"]
    assert got == [
        ("title", None, 0, "Only title"),
        ("paragraph", None, 0, "Para bold and link."),
        ("heading", 1, 0, "Sub"),
        ("list_item", None, 0, "a"),
        ("list_item", None, 1, "b"),
        ("list_item", None, 0, "c"),
        ("list_item", None, 0, "three"),
        ("code", None, 0, "x = 1"),
        ("heading", 1, 0, "Setext"),
    ]
    table = next(b for b in doc.blocks if b.kind == "table")
    assert table.attrs["rows"] == [["h", "i"], ["1", ""]]
    assert doc.blocks[6].attrs == {"ordered": True, "marker": "3."}
    assert next(b for b in doc.blocks if b.kind == "code").attrs == {"lang": "py"}


def test_markdown_several_h1_are_headings_not_titles():
    doc = MarkdownParser().parse(b"# A\n\ntext\n\n# B\n")
    assert [(b.kind, b.level) for b in doc.blocks] == [
        ("heading", 1),
        ("paragraph", None),
        ("heading", 1),
    ]


def test_markdown_front_matter_title_and_html_block():
    doc = MarkdownParser().parse(
        b"---\ntitle: 'Doc'\n---\n<table><tr><th>A</th></tr><tr><td>1</td></tr></table>\n"
    )
    assert doc.metadata == {"title": "Doc"}
    assert doc.blocks[0].kind == "table" and doc.blocks[0].attrs["rows"] == [["A"], ["1"]]


def test_strip_inline():
    assert strip_inline("**a** _b_ `c` ![d](e) <https://f> \\*g\\*") == "a b c d https://f *g*"


# ── html ─────────────────────────────────────────────────────────────


def test_html_skips_hidden_and_scripts_and_marks_chrome_as_furniture():
    blocks, meta = html_to_blocks(
        "<title>T</title><nav>menu</nav><main><p>one</p><p hidden>no</p><div style='display:none'>no</div>"
        "<script>no()</script><p aria-hidden='true'>no</p></main><footer>foot</footer>"
    )
    assert meta == {"title": "T"}
    assert [(b.kind, b.text) for b in blocks] == [
        ("page_header", "menu"),
        ("paragraph", "one"),
        ("page_footer", "foot"),
    ]


def test_html_table_spans_and_header_rows():
    blocks, _ = html_to_blocks(
        "<table><tr><th>a</th><th>b</th></tr><tr><td rowspan=2>x</td><td>1</td></tr><tr><td>2</td></tr>"
        "<tr><td colspan=2>wide</td></tr></table>"
    )
    assert blocks[0].attrs == {
        "rows": [["a", "b"], ["x", "1"], ["", "2"], ["wide", ""]],
        "header_rows": 1,
    }


def test_html_lists_nest_and_count_from_start():
    blocks, _ = html_to_blocks("<ol start=4><li>d<ul><li>inner</li></ul></li><li>e</li></ol>")
    assert [(b.text, b.depth, b.attrs) for b in blocks] == [
        ("d", 0, {"ordered": True, "marker": "4."}),
        ("inner", 1, {"ordered": False}),
        ("e", 0, {"ordered": True, "marker": "5."}),
    ]


def test_html_meta_charset_is_honoured():
    doc = HtmlParser().parse('<meta charset="windows-1252"><p>caf\xe9</p>'.encode("cp1252"))
    assert doc.blocks[0].text == "café"


# ── office ───────────────────────────────────────────────────────────


def test_docx_headings_lists_tables_furniture():
    doc = DocxParser().parse((DOCS / "handbook.docx").read_bytes())
    by_text = {b.text: b for b in doc.blocks}
    assert doc.metadata["title"] == "ACME Employee Handbook"
    assert by_text["Employee Handbook"].kind == "title"
    assert (by_text["1 Leave"].kind, by_text["1 Leave"].level) == ("heading", 1)
    assert by_text["1.1 Sick leave"].level == 2  # basedOn Heading2
    assert by_text["2.1 Thiết bị"].level == 3  # localised name, outlineLvl from the style
    assert by_text["Half days are allowed."].attrs == {"ordered": True, "marker": "2.a)"}
    assert by_text["Apply at least three days ahead."].attrs == {"ordered": False}
    assert by_text["Leave entitlements"].kind == "caption"
    assert "one day" not in " ".join(b.text for b in doc.blocks)  # w:del is skipped
    table = next(b for b in doc.blocks if b.kind == "table")
    assert table.attrs["rows"][2] == ["Sick", "30", ""]  # vMerge continuation
    assert table.attrs["rows"][3] == ["Unpaid leave by agreement", "", "No"]  # gridSpan
    assert (
        by_text["A boxed note: policies are reviewed every January."].kind == "paragraph"
    )  # 1x1 table unwrapped
    assert by_text["curl -X POST https://hr.example/api/leave"].kind == "code"
    assert by_text["Organisation chart of the HR team"].kind == "figure"
    assert {b.kind for b in doc.blocks if b.text in ("ACME Internal", "Confidential - page")} == {
        "page_header",
        "page_footer",
    }
    assert doc.blocks[-1].kind == "footnote"


def test_pptx_reading_order_titles_lists_regions():
    doc = PptxParser().parse((DOCS / "onboarding.pptx").read_bytes())
    assert [p.page_no for p in doc.pages] == [1, 2, 3]
    slide2 = [b for b in doc.blocks if b.attrs.get("slide") == 2]
    assert (
        slide2[0].kind == "heading" and slide2[0].text == "First week"
    )  # geometry beats XML order
    items = [(b.text, b.depth, b.attrs.get("marker")) for b in slide2 if b.kind == "list_item"]
    assert items == [("Collect your badge", 0, None), ("Floor 3 reception", 1, None),
                     ("Set up two-factor login", 0, None), ("Read the handbook", 0, "3.")]  # fmt: skip
    assert any(b.kind == "paragraph" and b.text == "Ask questions anytime" for b in slide2)
    assert next(b for b in doc.blocks if b.kind == "page_footer").text == "2"
    slide3 = [b.text for b in doc.blocks if b.attrs.get("slide") == 3]
    assert slide3 == [
        "Liên hệ",
        "Email: hr@acme.example",
        "Phòng nhân sự: tầng 3",
    ]  # group, left to right
    title = doc.blocks[0]
    assert title.regions[0].page_no == 1 and all(0 <= v <= 1 for v in title.regions[0].bbox)


def test_xlsx_regions_labels_hidden_sheets():
    doc = XlsxParser().parse((DOCS / "budget.xlsx").read_bytes())
    texts = [(b.kind, b.text, b.attrs.get("range")) for b in doc.blocks if b.kind != "table"]
    assert ("heading", "Secret", None) not in texts  # hidden sheet
    assert ("paragraph", "Budget 2026", "A1:C4") in texts  # section label above the table
    tables = [b.attrs for b in doc.blocks if b.kind == "table"]
    assert tables[0]["rows"] == [
        ["Item", "Q1", "Q2"],
        ["Laptops", "12000", "8000"],
        ["Licences", "", "1500.5"],
    ]
    assert tables[0]["range"] == "A1:C4" and tables[2]["sheet"] == "Phụ cấp"
    assert tables[2]["rows"][2] == ["Gửi xe", "150000"]  # inline string


def test_cell_refs_round_trip():
    assert parse_ref("B7") == (6, 1) and parse_ref("AA1") == (0, 26)
    assert all(parse_ref(cell_ref(r, c)) == (r, c) for r in (0, 9, 99) for c in (0, 25, 26, 701))


def test_office_parsers_reject_non_zip_and_zip_bombs():
    with pytest.raises(DocumentParseError, match="not a DOCX"):
        DocxParser().parse(b"not a zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * (65 << 20))
    with pytest.raises(DocumentParseError, match="zip bomb"):
        DocxParser().parse(buf.getvalue())


# ── router ───────────────────────────────────────────────────────────


def test_router_by_name_mime_and_content():
    r = ParserRouter()
    assert r.for_file(b"x", name="a.MD").name == "markdown"
    assert r.for_file(b"x", mime="text/html; charset=utf-8").name == "html"
    assert r.for_file(b"  %PDF-1.7").name == "pdf"
    assert sniff_mime((DOCS / "budget.xlsx").read_bytes()).endswith("spreadsheetml.sheet")
    with pytest.raises(UnsupportedFormatError, match="no parser"):
        r.for_file(b"\x00\x01", name="a.bin")
