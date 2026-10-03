"""Generate the golden PDFs, byte for byte reproducibly.

    uv run python tests/golden/make_pdfs.py

reportlab with ``invariant=1`` (no timestamps or random ids) and the DejaVu
fonts embedded, so the font names (and with them bold) survive into the text
layer. The PDFs are committed; ``test_generators_are_reproducible`` regenerates
them when the fonts are installed and compares bytes.

- ``two_column_report.pdf``: running header and footer, a full-width title and
  abstract, two columns with numbered headings, a bullet list, a paragraph that
  breaks mid-sentence from the left column into the right one and from page 1
  into page 2, and a footnote.
- ``table_report.pdf``: a ruled table with a caption above it, the paragraph that
  refers to it, a word hyphenated across two lines, and a borderless aligned table.
- ``chinh_sach_vi.pdf``: a Vietnamese policy with numbered headings, a numbered
  list and a sentence broken across two lines.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import List, Tuple

OUT = Path(__file__).parent / "docs"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
PAGE = (595.0, 842.0)  # A4, points


def _fonts() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for name, file in (
        ("DejaVu", "DejaVuSans.ttf"),
        ("DejaVu-Bold", "DejaVuSans-Bold.ttf"),
        ("DejaVuMono", "DejaVuSansMono.ttf"),
    ):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))


def _wrap(text: str, font: str, size: float, width: float) -> List[str]:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    lines: List[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if stringWidth(trial, font, size) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


class _Writer:
    """Draws flowing text into columns, a page at a time."""

    def __init__(self, canvas, columns: List[Tuple[float, float]], top: float, bottom: float, on_page=None):
        self.c = canvas
        self.columns = columns
        self.col = 0
        self.top = top
        self.bottom = bottom
        self.y = top
        self.on_page = on_page
        self.page = 1
        if on_page:
            on_page(canvas, self.page)

    def _next_column(self) -> None:
        self.col += 1
        if self.col >= len(self.columns):
            self.c.showPage()
            self.page += 1
            if self.on_page:
                self.on_page(self.c, self.page)
            self.col = 0
        self.y = self.top

    def lines(self, lines: List[str], font: str, size: float, leading: float, x_offset: float = 0.0, after: float = 6.0):
        for line in lines:
            if self.y - leading < self.bottom:
                self._next_column()
            self.y -= leading
            self.c.setFont(font, size)
            self.c.drawString(self.columns[self.col][0] + x_offset, self.y, line)
        self.y -= after

    def para(self, text: str, font: str = "DejaVu", size: float = 10, after: float = 6.0, indent: float = 0.0):
        x0, x1 = self.columns[self.col]
        self.lines(_wrap(text, font, size, x1 - x0 - indent), font, size, size * 1.25, indent, after)

    def heading(self, text: str, size: float = 12):
        if self.y - 3 * size < self.bottom:
            self._next_column()
        self.y -= 4
        self.para(text, "DejaVu-Bold", size, after=4)

    def bullet(self, text: str, marker: str = "•"):
        x0, x1 = self.columns[self.col]
        lines = _wrap(text, "DejaVu", 10, x1 - x0 - 14)
        first = True
        for line in lines:
            if self.y - 12.5 < self.bottom:
                self._next_column()
            self.y -= 12.5
            self.c.setFont("DejaVu", 10)
            if first:
                self.c.drawString(self.columns[self.col][0] + 2, self.y, marker)
                first = False
            self.c.drawString(self.columns[self.col][0] + 14, self.y, line)
        self.y -= 3


def _canvas(buf):
    from reportlab.pdfgen import canvas

    _fonts()
    return canvas.Canvas(buf, pagesize=PAGE, invariant=1)


LOREM = (
    "Retrieval quality depends on how documents are split. Chunks that respect the structure of "
    "the source keep headings, lists and tables together, so a passage that answers a question "
    "is found as one unit instead of three fragments."
)


def two_column_report(path: Path = None) -> bytes:
    buf = io.BytesIO()
    c = _canvas(buf)

    def furniture(canvas, page):
        canvas.setFont("DejaVu", 8)
        canvas.drawString(56, 810, "ACME Research - Annual Report 2026")
        canvas.drawString(290, 24, f"Page {page}")

    furniture(c, 1)
    c.setFont("DejaVu-Bold", 20)
    c.drawString(56, 760, "Structured Chunking for Enterprise Search")
    c.setFont("DejaVu", 10)
    y = 735
    abstract = (
        "Abstract. We study how the layout of business documents affects retrieval. Our pipeline "
        "keeps every character of a document addressable, so answers cite the exact page and box."
    )
    for line in _wrap(abstract, "DejaVu", 10, 483):
        c.drawString(56, y, line)
        y -= 12.5
    w = _Writer(c, [(56, 286), (309, 539)], top=y - 14, bottom=60, on_page=None)
    w.on_page = furniture
    w.heading("1 Introduction")
    w.para(LOREM + " " + LOREM)
    w.para("Three properties matter for a knowledge base:", after=2)
    w.bullet("every chunk maps back to a page region;")
    w.bullet("unchanged paragraphs are never embedded twice;")
    w.bullet("deletes leave no trace in any index.")
    w.heading("2 Method")
    w.heading("2.1 Layout analysis", size=11)
    w.para(LOREM)
    w.para(
        "Pages are read column by column. A paragraph that reaches the bottom of a column and "
        "continues at the top of the next one is joined again, so its sentence is not cut in "
        "half by the layout of the page and the reader of the chunk sees the whole statement "
        "that the author wrote, including the clause that ends after the column break and the "
        "final words that close the sentence on the following column"
    )
    w.heading("2.2 Chunking", size=11)
    for _ in range(7):
        w.para(LOREM)
    w.para(
        "The final paragraph of this section is long enough to run from the bottom of the second "
        "column of the first page onto the top of the first column of the second page, which tests "
        "the merge of a sentence across a page break as well as across columns, because readers "
        "expect the text to flow without interruption from one page to the next one"
    )
    w.heading("3 Results")
    w.para(LOREM)
    c.setFont("DejaVu", 7)
    c.drawString(w.columns[w.col][0], 48, "1 Measured on the internal handbook corpus, October 2026.")
    c.showPage()
    c.save()
    return _write(path, buf)


def table_report(path: Path = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    buf = io.BytesIO()
    c = _canvas(buf)
    c.setFont("DejaVu-Bold", 18)
    c.drawString(56, 780, "Leave Policy Summary")
    c.setFont("DejaVu", 10)
    y = 752
    for line in _wrap(
        "Table 1 lists the leave entitlements by contract type. Part-time staff receive leave pro "
        "rata to their hours.",
        "DejaVu",
        10,
        483,
    ):
        c.drawString(56, y, line)
        y -= 12.5
    c.setFont("DejaVu", 9)
    c.drawString(56, y - 8, "Table 1: Leave entitlements by contract")
    data = [
        ["Contract", "Annual leave", "Sick leave", "Notes"],
        ["Full-time", "12 days", "30 days", "Carry over until March"],
        ["Part-time", "Pro rata", "30 days", "Based on hours"],
        ["Intern", "6 days", "10 days", ""],
    ]
    table = Table(data, colWidths=[90, 90, 90, 180])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), "DejaVu"),
                ("FONTNAME", (0, 0), (-1, 0), "DejaVu-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    _, th = table.wrapOn(c, 483, 400)
    table_top = y - 16
    table.drawOn(c, 56, table_top - th)
    y = table_top - th - 24
    c.setFont("DejaVu-Bold", 12)
    c.drawString(56, y, "Allowances")
    y -= 18
    c.setFont("DejaVu", 10)
    c.drawString(56, y, "Monthly allowances are paid with the salary and adjusted every year accord-")
    y -= 12.5
    c.drawString(56, y, "ingly to the consumer price index.")
    y -= 22
    rows = [("Item", "Amount", "Frequency"), ("Lunch", "730,000", "monthly"), ("Parking", "150,000", "monthly"), ("Phone", "200,000", "monthly")]
    for item, amount, freq in rows:
        c.drawString(56, y, item)
        c.drawString(200, y, amount)
        c.drawString(330, y, freq)
        y -= 14
    c.showPage()
    c.save()
    return _write(path, buf)


def chinh_sach_vi(path: Path = None) -> bytes:
    buf = io.BytesIO()
    c = _canvas(buf)
    c.setFont("DejaVu-Bold", 18)
    c.drawString(56, 780, "Chính sách nghỉ phép năm 2026")
    w = _Writer(c, [(56, 539)], top=760, bottom=60)
    w.heading("1. Phạm vi áp dụng")
    w.para(
        "Chính sách này áp dụng cho toàn bộ nhân viên chính thức của công ty. Nhân viên thử việc "
        "được hưởng chế độ theo hợp đồng thử việc."
    )
    w.heading("2. Số ngày nghỉ phép")
    w.para(
        "Nhân viên được nghỉ phép 12 ngày làm việc mỗi năm. Cứ đủ 5 năm công tác, số ngày nghỉ "
        "phép được tăng thêm 1 ngày.",
        after=4,
    )
    w.bullet("Đăng ký nghỉ trên hệ thống trước ít nhất 3 ngày.", marker="1.")
    w.bullet("Quản lý trực tiếp phê duyệt trong vòng 2 ngày làm việc.", marker="2.")
    w.bullet("Phòng nhân sự cập nhật số ngày phép còn lại.", marker="3.")
    w.heading("3. Nghỉ ốm")
    c.setFont("DejaVu", 10)
    y = w.y - 12.5
    c.drawString(56, y, "Nghỉ ốm từ 2 ngày trở lên cần có giấy xác nhận của cơ sở y tế. Trường hợp đặc biệt,")
    c.drawString(56, y - 12.5, "giám đốc nhân sự quyết định.")
    c.showPage()
    c.save()
    return _write(path, buf)


def _write(path: Path, buf: io.BytesIO) -> bytes:
    data = buf.getvalue()
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return data


GENERATORS = {
    "two_column_report.pdf": two_column_report,
    "table_report.pdf": table_report,
    "chinh_sach_vi.pdf": chinh_sach_vi,
}


if __name__ == "__main__":
    for name, make in GENERATORS.items():
        make(OUT / name)
