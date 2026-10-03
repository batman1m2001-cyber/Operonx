"""A 200-document corpus for the K1 scale gates, generated reproducibly.

    uv run python tests/golden/make_corpus.py OUT_DIR [--docs 200]

A fictional company's knowledge base: HR, IT, finance and operations policies,
meeting notes and FAQs, in Markdown, HTML, plain text, DOCX and PDF, about a
fifth of it in Vietnamese. Every document has headings, paragraphs, lists and
most have a table, built from a seeded random generator so the same seed gives
the same bytes. :func:`long_pdf` makes the 100-page document of the
one-paragraph-edit gate. Nothing here is committed: the tests generate the
corpus into a temporary directory.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import random
from html import escape
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).parent

TEAMS = ["HR", "IT", "Finance", "Operations", "Legal", "Sales", "Support", "Engineering"]
TOPICS = {
    "HR": [
        "annual leave",
        "sick leave",
        "parental leave",
        "remote work",
        "onboarding",
        "performance reviews",
        "overtime",
    ],
    "IT": [
        "laptop policy",
        "password rotation",
        "VPN access",
        "software requests",
        "incident response",
        "backups",
    ],
    "Finance": [
        "expense claims",
        "travel booking",
        "purchase orders",
        "invoices",
        "per diem",
        "budget approval",
    ],
    "Operations": [
        "office access",
        "parking",
        "meeting rooms",
        "deliveries",
        "cleaning",
        "fire safety",
    ],
    "Legal": [
        "contracts",
        "data protection",
        "NDAs",
        "intellectual property",
        "compliance training",
    ],
    "Sales": ["discount approval", "CRM hygiene", "lead routing", "quotes", "commission"],
    "Support": ["ticket triage", "escalation", "SLA targets", "refunds", "customer notes"],
    "Engineering": [
        "code review",
        "release train",
        "on-call",
        "postmortems",
        "feature flags",
        "testing",
    ],
}
VERBS = ["must", "should", "may", "is expected to", "is required to"]
ACTS = ["submit the form", "notify the team lead", "record the request", "attach the receipt", "follow the checklist",
        "update the tracker", "confirm in writing", "ask the owner", "read the guide", "use the portal"]  # fmt: skip
WHENS = ["within two working days", "before the end of the month", "at least a week ahead", "on the same day",
         "before the quarter closes", "after approval", "every Monday", "once a year"]  # fmt: skip
PEOPLE = ["employees", "managers", "contractors", "new joiners", "team leads", "approvers"]
VI = [
    "Nhân viên cần gửi yêu cầu trên cổng thông tin nội bộ trước ít nhất ba ngày làm việc.",
    "Quản lý trực tiếp phê duyệt yêu cầu trong vòng hai ngày làm việc.",
    "Phòng nhân sự cập nhật số liệu vào cuối mỗi tháng.",
    "Mọi thắc mắc vui lòng liên hệ bộ phận hỗ trợ qua email.",
    "Chính sách này áp dụng cho toàn bộ nhân viên chính thức của công ty.",
    "Chi phí phát sinh được hoàn trả khi có hóa đơn hợp lệ.",
]
VI_HEADINGS = [
    "Phạm vi áp dụng",
    "Quy trình thực hiện",
    "Trách nhiệm",
    "Lưu ý",
    "Câu hỏi thường gặp",
]


def _sentence(rng: random.Random, topic: str) -> str:
    return (f"For {topic}, {rng.choice(PEOPLE)} {rng.choice(VERBS)} {rng.choice(ACTS)} "
            f"{rng.choice(WHENS)}, and the {rng.choice(TEAMS)} team keeps reference {rng.randint(100, 999)}.")  # fmt: skip


def _paragraph(rng: random.Random, topic: str, vi: bool) -> str:
    if vi:
        return " ".join(rng.sample(VI, rng.randint(2, 4)))
    return " ".join(_sentence(rng, topic) for _ in range(rng.randint(2, 5)))


Block = Tuple[str, object]  # ("h1"|"h2"|"p"|"ul"|"ol"|"table", payload)


def document(rng: random.Random, index: int) -> Tuple[str, List[Block], bool]:
    """A title and blocks for document ``index``."""
    team = TEAMS[index % len(TEAMS)]
    topic = rng.choice(TOPICS[team])
    vi = index % 5 == 0
    title = (
        f"{team} handbook: {topic} (v{index})"
        if not vi
        else f"Sổ tay {team}: {topic} (bản {index})"
    )
    blocks: List[Block] = [("p", _paragraph(rng, topic, vi))]
    for s in range(rng.randint(2, 5)):
        heading = (
            rng.choice(VI_HEADINGS)
            if vi
            else f"{s + 1} {topic.capitalize()} {rng.choice(['rules', 'process', 'exceptions', 'contacts', 'examples'])}"
        )
        blocks.append(("h1", heading))
        for _ in range(rng.randint(1, 3)):
            blocks.append(("p", _paragraph(rng, topic, vi)))
        if rng.random() < 0.6:
            items = [
                (rng.choice(VI) if vi else _sentence(rng, topic)) for _ in range(rng.randint(2, 5))
            ]
            blocks.append(("ol" if rng.random() < 0.4 else "ul", items))
        if rng.random() < 0.5:
            blocks.append(("h2", f"{s + 1}.1 {'Chi tiết' if vi else 'Details'}"))
            blocks.append(("p", _paragraph(rng, topic, vi)))
        if rng.random() < 0.4:
            header = (
                ["Item", "Owner", "Deadline"] if not vi else ["Hạng mục", "Phụ trách", "Thời hạn"]
            )
            rows = [
                [
                    f"{topic} {r + 1}",
                    rng.choice(TEAMS),
                    f"{rng.randint(1, 28)}/{rng.randint(1, 12)}/2026",
                ]
                for r in range(rng.randint(2, 6))
            ]
            blocks.append(("table", [header] + rows))
    return title, blocks, vi


def to_markdown(title: str, blocks: List[Block]) -> str:
    out = [f"# {title}", ""]
    for kind, payload in blocks:
        if kind == "h1":
            out += [f"## {payload}", ""]
        elif kind == "h2":
            out += [f"### {payload}", ""]
        elif kind == "p":
            out += [str(payload), ""]
        elif kind in ("ul", "ol"):
            out += [
                (f"{i + 1}. " if kind == "ol" else "- ") + item for i, item in enumerate(payload)
            ] + [""]
        else:
            rows = payload
            out += ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
            out += ["| " + " | ".join(r) + " |" for r in rows[1:]] + [""]
    return "\n".join(out)


def to_html(title: str, blocks: List[Block]) -> str:
    out = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{escape(title)}</title></head><body>",
           "<nav><a href='/'>Intranet</a> <a href='/policies'>Policies</a></nav><main>", f"<h1>{escape(title)}</h1>"]  # fmt: skip
    for kind, payload in blocks:
        if kind in ("h1", "h2"):
            tag = "h2" if kind == "h1" else "h3"
            out.append(f"<{tag}>{escape(str(payload))}</{tag}>")
        elif kind == "p":
            out.append(f"<p>{escape(str(payload))}</p>")
        elif kind in ("ul", "ol"):
            out.append(
                f"<{kind}>" + "".join(f"<li>{escape(i)}</li>" for i in payload) + f"</{kind}>"
            )
        else:
            rows = payload
            out.append("<table><tr>" + "".join(f"<th>{escape(c)}</th>" for c in rows[0]) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in rows[1:]) + "</table>")  # fmt: skip
    out.append("</main><footer>Internal use only</footer></body></html>")
    return "\n".join(out)


def to_text(title: str, blocks: List[Block]) -> str:
    out = [title, ""]
    for kind, payload in blocks:
        if kind == "table":
            out += ["  ".join(r) for r in payload] + [""]
        elif kind in ("ul", "ol"):
            out += [f"* {i}" for i in payload] + [""]
        else:
            out += [str(payload), ""]
    return "\n".join(out)


def _office():
    spec = importlib.util.spec_from_file_location("make_office", HERE / "make_office.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def to_docx(title: str, blocks: List[Block]) -> bytes:
    m = _office()
    body = [m._p(escape(title), "Title")]
    for kind, payload in blocks:
        if kind in ("h1", "h2"):
            body.append(m._p(escape(str(payload)), "Heading1" if kind == "h1" else "Heading2"))
        elif kind == "p":
            body.append(m._p(escape(str(payload))))
        elif kind in ("ul", "ol"):
            body += [m._p(escape(i), num=("2" if kind == "ol" else "1", 0)) for i in payload]
        else:
            cell = lambda t: f"<w:tc><w:p><w:r><w:t>{escape(t)}</w:t></w:r></w:p></w:tc>"  # noqa: E731
            body.append(
                "<w:tbl>"
                + "".join("<w:tr>" + "".join(cell(c) for c in r) + "</w:tr>" for r in payload)
                + "</w:tbl>"
            )
    styles = (f'{m.XML}<w:styles xmlns:w="{m.W}"><w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style></w:styles>')  # fmt: skip
    numbering = (f'{m.XML}<w:numbering xmlns:w="{m.W}">'
                 '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/></w:lvl></w:abstractNum>'
                 '<w:abstractNum w:abstractNumId="2"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>'
                 '<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="2"/></w:num></w:numbering>')  # fmt: skip
    base = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    wml = "application/vnd.openxmlformats-officedocument.wordprocessingml."
    return m._zip(None, {
        "[Content_Types].xml": m._types({"word/document.xml": wml + "document.main+xml", "word/styles.xml": wml + "styles+xml",
                                         "word/numbering.xml": wml + "numbering+xml"}),
        "_rels/.rels": m._rels(("rId1", base + "officeDocument", "word/document.xml")),
        "word/_rels/document.xml.rels": m._rels(("rId1", base + "styles", "styles.xml"), ("rId2", base + "numbering", "numbering.xml")),
        "word/document.xml": f'{m.XML}<w:document xmlns:w="{m.W}"><w:body>{"".join(body)}<w:sectPr/></w:body></w:document>',
        "word/styles.xml": styles,
        "word/numbering.xml": numbering,
    })  # fmt: skip


def _pdfs():
    spec = importlib.util.spec_from_file_location("make_pdfs", HERE / "make_pdfs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def to_pdf(title: str, blocks: List[Block], columns: int = 1) -> bytes:
    m = _pdfs()
    buf = io.BytesIO()
    c = m._canvas(buf)
    c.setFont("DejaVu-Bold", 18)
    c.drawString(56, 780, title[:60])
    cols = [(56, 539)] if columns == 1 else [(56, 286), (309, 539)]
    w = m._Writer(c, cols, top=760, bottom=60)
    for kind, payload in blocks:
        if kind == "h1":
            w.heading(str(payload))
        elif kind == "h2":
            w.heading(str(payload), size=11)
        elif kind == "p":
            w.para(str(payload))
        elif kind in ("ul", "ol"):
            for i, item in enumerate(payload):
                w.bullet(item, marker=f"{i + 1}." if kind == "ol" else "•")
        else:
            for row in payload:
                w.para("    ".join(row))
    c.showPage()
    c.save()
    return buf.getvalue()


def long_pdf(pages: int = 100, edited: Optional[int] = None) -> bytes:
    """A two-column ``pages``-page report; ``edited`` rewrites one paragraph (that section's)."""
    rng = random.Random(4242)
    blocks: List[Block] = []
    section = 0
    while len(blocks) < pages * 13:
        section += 1
        blocks.append(("h1", f"{section} Section on {rng.choice(TOPICS['Engineering'])}"))
        for k in range(3):
            text = _paragraph(rng, "release management", False)
            if edited == section and k == 1:
                text = text.replace(
                    "For release management", "For the revised release management", 1
                )
            blocks.append(("p", text))
    data = to_pdf("Engineering operations manual", blocks, columns=2)
    return data


FORMATS = [(".md", 60), (".html", 50), (".txt", 40), (".docx", 30), (".pdf", 20)]


def corpus(n_docs: int = 200, seed: int = 2026) -> Dict[str, bytes]:
    """``file name -> bytes`` for ``n_docs`` documents (the format mix scales with n)."""
    rng = random.Random(seed)
    plan: List[str] = []
    total = sum(k for _, k in FORMATS)
    for ext, k in FORMATS:
        plan += [ext] * round(k * n_docs / total)
    plan = (plan + [".md"] * n_docs)[:n_docs]
    out: Dict[str, bytes] = {}
    for i, ext in enumerate(plan):
        title, blocks, _ = document(rng, i)
        name = f"doc_{i:03d}{ext}"
        if ext == ".md":
            out[name] = to_markdown(title, blocks).encode("utf-8")
        elif ext == ".html":
            out[name] = to_html(title, blocks).encode("utf-8")
        elif ext == ".txt":
            out[name] = to_text(title, blocks).encode("utf-8")
        elif ext == ".docx":
            out[name] = to_docx(title, blocks)
        else:
            out[name] = to_pdf(title, blocks, columns=1 + i % 2)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--docs", type=int, default=200)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, data in corpus(args.docs).items():
        (args.out / name).write_bytes(data)
