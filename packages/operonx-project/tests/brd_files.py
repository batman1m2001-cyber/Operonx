"""Real files for the readers' tests, made here so nothing private is committed.

- :func:`make_docx`: a Word document with numbered headings, a paragraph, a
  list, a table and an embedded PNG figure, written as Office XML;
- :func:`make_scan_pdf`: a PDF page that is only an image of text (a scan:
  no text layer), via Pillow;
- :func:`make_text_png`: a PNG of rendered text, for OCR;
- :func:`make_zip`: a kit with a Markdown file and an image.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import List, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def make_text_png(path: Path, lines: Sequence[str], size: Tuple[int, int] = (1400, 400)) -> Path:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, 40)
    for i, line in enumerate(lines):
        draw.text((40, 40 + i * 70), line, fill="black", font=font)
    img.save(path)
    return path


def make_scan_pdf(path: Path, lines: Sequence[str]) -> Path:
    png = make_text_png(path.with_suffix(".png"), lines, size=(1700, 600))
    Image.open(png).convert("RGB").save(path, "PDF", resolution=150.0)
    png.unlink()
    return path


def _p(text: str, style: str = "") -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def _table(rows: List[List[str]]) -> str:
    body = "".join("<w:tr>" + "".join(f"<w:tc>{_p(c)}</w:tc>" for c in row) + "</w:tr>" for row in rows)
    return f"<w:tbl>{body}</w:tbl>"


def _figure(rid: str, caption: str) -> str:
    return (
        '<w:p><w:r><w:drawing><wp:inline><wp:docPr id="1" name="Picture 1" descr="' + caption + '"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/></pic:blipFill></pic:pic></a:graphicData></a:graphic>'
        "</wp:inline></w:drawing></w:r></w:p>"
    )


_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>
<w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>
<w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>
</w:styles>"""


def make_docx(path: Path, blocks: Sequence[Tuple[str, object]], image: bytes = b"") -> Path:
    """*blocks*: ("h1"|"h2", text), ("p", text), ("table", rows), ("figure", caption)."""
    parts = []
    for kind, value in blocks:
        if kind == "h1":
            parts.append(_p(str(value), "Heading1"))
        elif kind == "h2":
            parts.append(_p(str(value), "Heading2"))
        elif kind == "table":
            parts.append(_table(value))  # type: ignore[arg-type]
        elif kind == "figure":
            parts.append(_figure("rId9", str(value)))
        else:
            parts.append(_p(str(value)))
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f"<w:body>{''.join(parts)}</w:body></w:document>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        'Target="media/image1.png"/></Relationships>'
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'officeDocument" Target="word/document.xml"/></Relationships>'
    )
    if not image:
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(buf, "PNG")
        image = buf.getvalue()
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("word/media/image1.png", image)
    return path


def make_zip(path: Path, files: dict) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


#: A small BRD, Vietnamese like the real ones: §1 descriptive, §2 a table of codes, §3 a rule in prose.
SAMPLE_BLOCKS = [
    ("h1", "Đặc tả hệ thống nhắc lịch"),
    ("h2", "1. Tóm tắt"),
    ("p", "Tài liệu này mô tả hệ thống nhắc lịch học cho phụ huynh."),
    ("h2", "2. Yêu cầu kinh doanh"),
    ("table", [["Mã", "Yêu cầu"], ["BR-01", "Gửi lời nhắc trước buổi học 24 giờ"],
               ["BR-02", "Phụ huynh có thể hủy nhắc bằng một tin trả lời"]]),
    ("h2", "3. Quy tắc"),
    ("p", "Không gọi điện cho phụ huynh sau 21 giờ. Mỗi phụ huynh nhận tối đa hai cuộc gọi mỗi ngày."),
    ("figure", "luồng nhắc lịch · 3 bước"),
]  # fmt: skip
