"""Generate the golden Office documents, byte for byte reproducibly.

    uv run python tests/golden/make_office.py

Writes ``tests/golden/docs/{handbook.docx, onboarding.pptx, budget.xlsx}``. The
packages are written by hand with ``zipfile`` (fixed timestamps, fixed member
order) so the bytes never change; ``test_generators_are_reproducible`` checks it.
They are small but realistic: styles with ``basedOn`` chains, numbering
definitions, merged cells, placeholders inherited from the layout, a Vietnamese
section.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Dict

OUT = Path(__file__).parent / "docs"
_STAMP = (2026, 1, 1, 0, 0, 0)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _zip(path: Path, members: Dict[str, str]) -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in members.items():
            info = zipfile.ZipInfo(name, date_time=_STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, text.encode("utf-8"))
    data = buf.getvalue()
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return data


def _rels(*rels) -> str:
    body = "".join(
        f'<Relationship Id="{i}" Type="{t}" Target="{target}"/>' for i, t, target in rels
    )
    return f'{XML}<Relationships xmlns="{PR}">{body}</Relationships>'


def _types(overrides: Dict[str, str]) -> str:
    body = "".join(f'<Override PartName="/{p}" ContentType="{t}"/>' for p, t in overrides.items())
    return (
        f'{XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        f'<Default Extension="xml" ContentType="application/xml"/>{body}</Types>'
    )


# ── DOCX ────────────────────────────────────────────────────────────────


def _p(text: str, style: str = None, num: tuple = None, extra: str = "") -> str:
    ppr = ""
    if style or num:
        ppr = "<w:pPr>"
        if style:
            ppr += f'<w:pStyle w:val="{style}"/>'
        if num:
            ppr += f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>'
        ppr += "</w:pPr>"
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r>{extra}</w:p>'


def docx(path: Path = None) -> bytes:
    styles = f"""{XML}<w:styles xmlns:w="{W}">
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="PolicyHeading"><w:name w:val="Policy Heading"/><w:basedOn w:val="Heading2"/></w:style>
<w:style w:type="paragraph" w:styleId="Tieude3"><w:name w:val="Tiêu đề 3"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="SourceCode"><w:name w:val="Source Code"/><w:basedOn w:val="Normal"/></w:style>
</w:styles>"""
    numbering = f"""{XML}<w:numbering xmlns:w="{W}">
<w:abstractNum w:abstractNumId="10"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/></w:lvl><w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="o"/></w:lvl></w:abstractNum>
<w:abstractNum w:abstractNumId="20"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl><w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%1.%2)"/></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="10"/></w:num>
<w:num w:numId="2"><w:abstractNumId w:val="20"/></w:num>
</w:numbering>"""
    image = (
        f'<w:r><w:drawing><wp:inline><wp:docPr id="1" name="Picture 1" descr="Organisation chart of the HR team"/>'
        f'<a:graphic><a:graphicData uri="{PIC}"><pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="org.png"/></pic:nvPicPr>'
        f"</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )
    table = (
        "<w:tbl>"
        "<w:tr><w:tc><w:p><w:r><w:t>Leave type</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Days</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Paid</w:t></w:r></w:p></w:tc></w:tr>"
        '<w:tr><w:tc><w:p><w:r><w:t>Annual</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>12</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Yes</w:t></w:r></w:p></w:tc></w:tr>'
        "<w:tr><w:tc><w:p><w:r><w:t>Sick</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>30</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc></w:tr>"
        '<w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>Unpaid leave by agreement</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>No</w:t></w:r></w:p></w:tc></w:tr>'
        "</w:tbl>"
    )
    single = (
        "<w:tbl><w:tr><w:tc>"
        + _p("A boxed note: policies are reviewed every January.")
        + "</w:tc></w:tr></w:tbl>"
    )
    body = "".join(
        [
            _p("Employee Handbook", "Title"),
            _p("This handbook explains how leave, benefits and equipment work at ACME."),
            _p("1 Leave", "Heading1"),
            _p("Every full-time employee receives annual leave. Apply through the HR portal."),
            _p("Apply at least three days ahead.", "ListBullet"),
            _p("Your manager approves the request.", num=("1", 1)),
            _p("Unused days carry over until March.", "ListBullet"),
            _p("Leave entitlements", "Caption"),
            table,
            _p("1.1 Sick leave", "PolicyHeading"),
            _p(
                "Sick leave needs a doctor's note after two days.",
                extra="<w:del><w:r><w:delText>one day</w:delText></w:r></w:del>",
            ),
            _p("Steps to request leave:"),
            _p("Open the portal.", num=("2", 0)),
            _p("Choose the dates.", num=("2", 0)),
            _p("Half days are allowed.", num=("2", 1)),
            _p("Submit.", num=("2", 0)),
            _p("2 Phúc lợi", "Heading1"),
            _p(
                "Nhân viên được hỗ trợ ăn trưa và gửi xe.",
                extra="<w:r><w:br/><w:t>Mức hỗ trợ cập nhật hằng năm.</w:t></w:r>",
            ),
            _p("2.1 Thiết bị", "Tieude3"),
            _p("Each employee gets a laptop.", extra=image),
            single,
            _p("curl -X POST https://hr.example/api/leave", "SourceCode"),
        ]
    )
    document = f'{XML}<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:wp="{WP}" xmlns:a="{A}" xmlns:pic="{PIC}"><w:body>{body}<w:sectPr/></w:body></w:document>'
    header = f'{XML}<w:hdr xmlns:w="{W}">{_p("ACME Internal")}</w:hdr>'
    footer = f'{XML}<w:ftr xmlns:w="{W}">{_p("Confidential - page")}</w:ftr>'
    footnotes = (
        f'{XML}<w:footnotes xmlns:w="{W}"><w:footnote w:id="-1"><w:p/></w:footnote><w:footnote w:id="0"><w:p/></w:footnote>'
        f'<w:footnote w:id="1">{_p("Part-time staff receive leave pro rata.")}</w:footnote></w:footnotes>'
    )
    core = (
        f'{XML}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>ACME Employee Handbook</dc:title></cp:coreProperties>'
    )
    base = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    wml = "application/vnd.openxmlformats-officedocument.wordprocessingml."
    return _zip(
        path,
        {
            "[Content_Types].xml": _types(
                {
                    "word/document.xml": wml + "document.main+xml",
                    "word/styles.xml": wml + "styles+xml",
                    "word/numbering.xml": wml + "numbering+xml",
                    "word/header1.xml": wml + "header+xml",
                    "word/footer1.xml": wml + "footer+xml",
                    "word/footnotes.xml": wml + "footnotes+xml",
                    "docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
                }
            ),
            "_rels/.rels": _rels(
                ("rId1", base + "officeDocument", "word/document.xml"),
                (
                    "rId2",
                    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                    "docProps/core.xml",
                ),
            ),
            "word/_rels/document.xml.rels": _rels(
                ("rId1", base + "styles", "styles.xml"),
                ("rId2", base + "numbering", "numbering.xml"),
                ("rId3", base + "header", "header1.xml"),
                ("rId4", base + "footer", "footer1.xml"),
                ("rId5", base + "footnotes", "footnotes.xml"),
            ),
            "word/document.xml": document,
            "word/styles.xml": styles,
            "word/numbering.xml": numbering,
            "word/header1.xml": header,
            "word/footer1.xml": footer,
            "word/footnotes.xml": footnotes,
            "docProps/core.xml": core,
        },
    )


# ── PPTX ────────────────────────────────────────────────────────────────


def _sp(text_paras: str, ph: str = None, xfrm: tuple = None, idx: str = None) -> str:
    phx = ""
    if ph is not None or idx is not None:
        attrs = (f' type="{ph}"' if ph else "") + (f' idx="{idx}"' if idx else "")
        phx = f"<p:ph{attrs}/>"
    sppr = "<p:spPr/>"
    if xfrm:
        x, y, cx, cy = xfrm
        sppr = f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm></p:spPr>'
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="s"/><p:cNvSpPr/><p:nvPr>{phx}</p:nvPr></p:nvSpPr>{sppr}'
        f"<p:txBody><a:bodyPr/>{text_paras}</p:txBody></p:sp>"
    )


def _ap(text: str, lvl: int = 0, bullet: str = "") -> str:
    ppr = f'<a:pPr lvl="{lvl}">{bullet}</a:pPr>' if (lvl or bullet) else ""
    return f"<a:p>{ppr}<a:r><a:t>{text}</a:t></a:r></a:p>"


def pptx(path: Path = None) -> bytes:
    ns = f'xmlns:a="{A}" xmlns:p="{P}" xmlns:r="{R}"'
    layout = (
        f"{XML}<p:sldLayout {ns}><p:cSld><p:spTree>"
        + _sp("", "title", (457200, 274638, 8229600, 1143000))
        + _sp("", None, (457200, 1600200, 8229600, 4525963), idx="1")
        + _sp("", "sldNum", (6553200, 6356350, 2133600, 365125), idx="12")
        + "</p:spTree></p:cSld></p:sldLayout>"
    )
    master = f"{XML}<p:sldMaster {ns}><p:cSld><p:spTree/></p:cSld></p:sldMaster>"
    slide1 = (
        f"{XML}<p:sld {ns}><p:cSld><p:spTree>"
        + _sp(_ap("Welcome to ACME"), "ctrTitle", (685800, 2130425, 7772400, 1470025))
        + _sp(
            _ap("Onboarding for new staff, 2026"),
            "subTitle",
            (1371600, 3886200, 6400800, 1752600),
            idx="1",
        )
        + "</p:spTree></p:cSld></p:sld>"
    )
    table = (
        '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="5" name="t"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>'
        '<p:xfrm><a:off x="457200" y="4800000"/><a:ext cx="8229600" cy="1000000"/></p:xfrm>'
        "<a:graphic><a:graphicData><a:tbl>"
        "<a:tr><a:tc><a:txBody><a:p><a:r><a:t>Day</a:t></a:r></a:p></a:txBody></a:tc><a:tc><a:txBody><a:p><a:r><a:t>Topic</a:t></a:r></a:p></a:txBody></a:tc></a:tr>"
        "<a:tr><a:tc><a:txBody><a:p><a:r><a:t>1</a:t></a:r></a:p></a:txBody></a:tc><a:tc><a:txBody><a:p><a:r><a:t>Accounts and laptop</a:t></a:r></a:p></a:txBody></a:tc></a:tr>"
        '<a:tr><a:tc gridSpan="2"><a:txBody><a:p><a:r><a:t>Week 2: shadowing</a:t></a:r></a:p></a:txBody></a:tc><a:tc hMerge="1"><a:txBody><a:p/></a:txBody></a:tc></a:tr>'
        "</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    )
    slide2 = (
        f"{XML}<p:sld {ns}><p:cSld><p:spTree>"
        # Body first in XML, title second: order must come from geometry.
        + _sp(
            _ap("Collect your badge")
            + _ap("Floor 3 reception", lvl=1)
            + _ap("Set up two-factor login")
            + _ap("Read the handbook", bullet='<a:buAutoNum type="arabicPeriod" startAt="3"/>')
            + _ap("Ask questions anytime", bullet="<a:buNone/>"),
            None,
            None,
            idx="1",
        )
        + _sp(_ap("First week"), "title")
        + table
        + _sp(_ap("2"), "sldNum", None, idx="12")
        + '<p:pic><p:nvPicPr><p:cNvPr id="7" name="map" descr="Office floor map"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>'
        + '<p:blipFill/><p:spPr><a:xfrm><a:off x="6000000" y="300000"/><a:ext cx="2000000" cy="900000"/></a:xfrm></p:spPr></p:pic>'
        + "</p:spTree></p:cSld></p:sld>"
    )
    slide3 = (
        f"{XML}<p:sld {ns}><p:cSld><p:spTree>"
        + _sp(_ap("Liên hệ"), "title")
        + '<p:grpSp><p:nvGrpSpPr><p:cNvPr id="9" name="g"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        + '<p:grpSpPr><a:xfrm><a:off x="457200" y="1600200"/><a:ext cx="8000000" cy="2000000"/><a:chOff x="0" y="0"/><a:chExt cx="8000000" cy="2000000"/></a:xfrm></p:grpSpPr>'
        + _sp(_ap("Phòng nhân sự: tầng 3"), None, (4000000, 0, 4000000, 500000))
        + _sp(_ap("Email: hr@acme.example"), None, (0, 0, 3900000, 500000))
        + "</p:grpSp></p:spTree></p:cSld></p:sld>"
    )
    pres = (
        f'{XML}<p:presentation {ns}><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId9"/></p:sldMasterIdLst>'
        '<p:sldIdLst><p:sldId id="256" r:id="rId1"/><p:sldId id="257" r:id="rId2"/><p:sldId id="258" r:id="rId3"/></p:sldIdLst>'
        '<p:sldSz cx="9144000" cy="6858000"/></p:presentation>'
    )
    base = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    pml = "application/vnd.openxmlformats-officedocument.presentationml."
    slide_rels = _rels(("rId1", base + "slideLayout", "../slideLayouts/slideLayout1.xml"))
    return _zip(
        path,
        {
            "[Content_Types].xml": _types(
                {
                    "ppt/presentation.xml": pml + "presentation.main+xml",
                    "ppt/slides/slide1.xml": pml + "slide+xml",
                    "ppt/slides/slide2.xml": pml + "slide+xml",
                    "ppt/slides/slide3.xml": pml + "slide+xml",
                    "ppt/slideLayouts/slideLayout1.xml": pml + "slideLayout+xml",
                    "ppt/slideMasters/slideMaster1.xml": pml + "slideMaster+xml",
                }
            ),
            "_rels/.rels": _rels(("rId1", base + "officeDocument", "ppt/presentation.xml")),
            "ppt/_rels/presentation.xml.rels": _rels(
                ("rId1", base + "slide", "slides/slide1.xml"),
                ("rId2", base + "slide", "slides/slide2.xml"),
                ("rId3", base + "slide", "slides/slide3.xml"),
                ("rId9", base + "slideMaster", "slideMasters/slideMaster1.xml"),
            ),
            "ppt/presentation.xml": pres,
            "ppt/slides/slide1.xml": slide1,
            "ppt/slides/slide2.xml": slide2,
            "ppt/slides/slide3.xml": slide3,
            "ppt/slides/_rels/slide1.xml.rels": slide_rels,
            "ppt/slides/_rels/slide2.xml.rels": slide_rels,
            "ppt/slides/_rels/slide3.xml.rels": slide_rels,
            "ppt/slideLayouts/slideLayout1.xml": layout,
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _rels(
                ("rId1", base + "slideMaster", "../slideMasters/slideMaster1.xml")
            ),
            "ppt/slideMasters/slideMaster1.xml": master,
        },
    )


# ── XLSX ────────────────────────────────────────────────────────────────


def xlsx(path: Path = None) -> bytes:
    strings = [
        "Budget 2026",
        "Item",
        "Q1",
        "Q2",
        "Laptops",
        "Licences",
        "Notes",
        "Approved by finance",
        "Hidden",
        "Chi phí",
        "Số tiền",
        "Ăn trưa",
    ]
    sst = (
        f'{XML}<sst xmlns="{S}" count="{len(strings)}" uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{s}</t></si>" for s in strings)
        + "</sst>"
    )

    def c(ref, value, string=True):
        if string:
            return f'<c r="{ref}" t="s"><v>{strings.index(value)}</v></c>'
        return f'<c r="{ref}"><v>{value}</v></c>'

    sheet1 = (
        f'{XML}<worksheet xmlns="{S}"><sheetData>'
        f'<row r="1">{c("A1", "Budget 2026")}</row>'
        f'<row r="2">{c("A2", "Item")}{c("B2", "Q1")}{c("C2", "Q2")}</row>'
        f'<row r="3">{c("A3", "Laptops")}{c("B3", "12000", False)}{c("C3", "8000", False)}</row>'
        f'<row r="4">{c("A4", "Licences")}{c("C4", "1500.5", False)}</row>'
        f'<row r="7">{c("E7", "Notes")}</row>'
        f'<row r="8">{c("E8", "Approved by finance")}</row>'
        f'<row r="10">{c("A10", "Approved by finance")}</row>'
        '</sheetData><mergeCells count="1"><mergeCell ref="A1:C1"/></mergeCells></worksheet>'
    )
    sheet2 = f'{XML}<worksheet xmlns="{S}"><sheetData><row r="1">{c("A1", "Hidden")}</row></sheetData></worksheet>'
    sheet3 = (
        f'{XML}<worksheet xmlns="{S}"><sheetData>'
        f'<row r="2">{c("B2", "Chi phí")}{c("C2", "Số tiền")}</row>'
        f'<row r="3">{c("B3", "Ăn trưa")}{c("C3", "730000", False)}</row>'
        '<row r="4"><c r="B4" t="inlineStr"><is><t>Gửi xe</t></is></c><c r="C4"><v>150000</v></c></row>'
        '<row r="5"><c r="B5" t="b"><v>1</v></c></row>'
        "</sheetData></worksheet>"
    )
    book = (
        f'{XML}<workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
        '<sheet name="Budget" sheetId="1" r:id="rId1"/><sheet name="Secret" sheetId="2" state="hidden" r:id="rId2"/>'
        '<sheet name="Phụ cấp" sheetId="3" r:id="rId3"/></sheets></workbook>'
    )
    base = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    sml = "application/vnd.openxmlformats-officedocument.spreadsheetml."
    return _zip(
        path,
        {
            "[Content_Types].xml": _types(
                {
                    "xl/workbook.xml": sml + "sheet.main+xml",
                    "xl/worksheets/sheet1.xml": sml + "worksheet+xml",
                    "xl/worksheets/sheet2.xml": sml + "worksheet+xml",
                    "xl/worksheets/sheet3.xml": sml + "worksheet+xml",
                    "xl/sharedStrings.xml": sml + "sharedStrings+xml",
                }
            ),
            "_rels/.rels": _rels(("rId1", base + "officeDocument", "xl/workbook.xml")),
            "xl/_rels/workbook.xml.rels": _rels(
                ("rId1", base + "worksheet", "worksheets/sheet1.xml"),
                ("rId2", base + "worksheet", "worksheets/sheet2.xml"),
                ("rId3", base + "worksheet", "worksheets/sheet3.xml"),
                ("rId4", base + "sharedStrings", "sharedStrings.xml"),
            ),
            "xl/workbook.xml": book,
            "xl/worksheets/sheet1.xml": sheet1,
            "xl/worksheets/sheet2.xml": sheet2,
            "xl/worksheets/sheet3.xml": sheet3,
            "xl/sharedStrings.xml": sst,
        },
    )


GENERATORS = {"handbook.docx": docx, "onboarding.pptx": pptx, "budget.xlsx": xlsx}


if __name__ == "__main__":
    for name, make in GENERATORS.items():
        make(OUT / name)
