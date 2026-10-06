"""A 200-document corpus for the K1 scale gates, generated reproducibly.

    uv run python tests/golden/make_corpus.py OUT_DIR [--docs 200]

A fictional company's knowledge base: HR, IT, finance and operations policies,
meeting notes and FAQs, in Markdown, HTML, plain text, DOCX and PDF, about a
fifth of it in Vietnamese. Every document has headings, paragraphs, lists and
most have a table, built from a seeded random generator so the same seed gives
the same bytes. :func:`long_pdf` makes the 100-page document of the
one-paragraph-edit gate. Nothing here is committed: the tests generate the
corpus into a temporary directory.

Each Vietnamese document also states three facts of its own unit (a team and
a region, unique per document): :func:`vi_facts`. They come from a generator
seeded by the document's index, so the other documents are unchanged, and
:func:`vi_cases` derives the K2 Vietnamese eval set from them mechanically: the
question of a fact's template, the fact sentence as the quote-anchored label,
the fact's value as the answer.
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
VI_TEAMS = {
    "HR": "Nhân sự",
    "IT": "Công nghệ thông tin",
    "Finance": "Tài chính",
    "Operations": "Vận hành",
    "Legal": "Pháp chế",
    "Sales": "Kinh doanh",
    "Support": "Chăm sóc khách hàng",
    "Engineering": "Kỹ thuật",
}
VI_REGIONS = ["miền Bắc", "miền Trung", "miền Nam", "Tây Nguyên", "đồng bằng sông Cửu Long"]
_MONEY = ["150.000", "200.000", "250.000", "300.000", "350.000", "400.000", "500.000"]
_BIG = ["5 triệu", "10 triệu", "20 triệu", "50 triệu", "100 triệu"]
_APPS = ["Lịch Chung", "Bàn Làm Việc", "Hẹn Gặp", "Phòng Xanh", "Đặt Chỗ"]
_CITIES = ["Hà Nội", "Đà Nẵng", "Thành phố Hồ Chí Minh", "Cần Thơ", "Hải Phòng"]
#: (name, statement, question, answer, slots): ``{u}`` is the unit, ``{U}`` the unit
#: capitalised; each slot draws from its list.
VI_FACTS = [
    ("nghi_phep", "Nhân viên {u} được nghỉ phép năm {n} ngày làm việc.",
     "Người làm việc ở {u} có bao nhiêu ngày phép mỗi năm?", "{n} ngày làm việc",
     {"n": [str(x) for x in range(12, 21)]}),
    ("taxi", "Chi phí đi taxi khi công tác của {u} được hoàn tối đa {m} đồng mỗi chuyến.",
     "{U} được hoàn bao nhiêu tiền cho một chuyến taxi đi công tác?", "{m} đồng", {"m": _MONEY}),
    ("mat_khau", "Mật khẩu hệ thống của {u} phải được thay đổi sau mỗi {n} ngày.",
     "Bao lâu thì {u} phải đổi mật khẩu hệ thống một lần?", "{n} ngày",
     {"n": ["30", "45", "60", "90", "120"]}),
    ("mua_hang", "Mọi đơn mua hàng của {u} có giá trị trên {b} đồng cần giám đốc khối phê duyệt.",
     "Đơn mua hàng của {u} từ mức giá nào thì phải có giám đốc khối duyệt?", "{b} đồng",
     {"b": _BIG}),
    ("phong_hop", "Phòng họp của {u} nằm ở tầng {f} và được đặt qua ứng dụng {a}.",
     "Muốn đặt phòng họp của {u} thì dùng ứng dụng nào?", "ứng dụng {a}",
     {"f": [str(x) for x in range(2, 16)], "a": _APPS}),
    ("ca_truc", "Ca trực hỗ trợ của {u} bắt đầu lúc {h} giờ sáng và kết thúc lúc {e} giờ tối.",
     "Ca trực hỗ trợ ở {u} kéo dài từ mấy giờ đến mấy giờ?", "từ {h} giờ sáng đến {e} giờ tối",
     {"h": ["6", "7", "8"], "e": ["8", "9", "10"]}),
    ("may_tinh", "Nhân viên mới của {u} nhận máy tính xách tay trong vòng {n} ngày kể từ ngày đi làm.",
     "Sau bao lâu thì người mới vào {u} được cấp máy tính?", "{n} ngày",
     {"n": ["2", "3", "5", "7"]}),
    ("hop_dong", "Hợp đồng của {u} có giá trị trên {b} đồng phải được phòng pháp chế rà soát trong {n} ngày.",
     "Phòng pháp chế cần bao nhiêu ngày để rà soát hợp đồng lớn của {u}?", "{n} ngày",
     {"b": _BIG, "n": ["3", "5", "7", "10"]}),
    ("an_trua", "Phụ cấp ăn trưa của {u} là {m} đồng cho mỗi ngày làm việc tại văn phòng.",
     "Mỗi ngày đi làm ở văn phòng, {u} được phụ cấp bữa trưa bao nhiêu?", "{m} đồng",
     {"m": ["30.000", "35.000", "40.000", "45.000", "50.000"]}),
    ("su_co", "Khi có sự cố bảo mật, nhân viên {u} phải gọi số máy lẻ {x} trong vòng {n} phút.",
     "Nhân viên {u} gặp sự cố bảo mật thì gọi số máy lẻ nào?", "số máy lẻ {x}",
     {"x": [str(x) for x in range(1100, 1200, 7)], "n": ["10", "15", "30"]}),
    ("lam_them", "Giờ làm thêm vào ngày lễ của {u} được trả {p} phần trăm lương cơ bản.",
     "Làm thêm giờ ngày lễ ở {u} được tính lương thế nào?", "{p} phần trăm lương cơ bản",
     {"p": ["200", "250", "300", "400"]}),
    ("sao_luu", "Dữ liệu sao lưu của {u} được lưu giữ trong {n} tháng tại trung tâm dữ liệu {c}.",
     "{U} giữ bản sao lưu dữ liệu bao lâu và ở đâu?", "{n} tháng tại {c}",
     {"n": ["6", "12", "18", "24", "36"], "c": _CITIES}),
    ("dao_tao", "Mỗi năm, nhân viên {u} phải hoàn thành ít nhất {n} giờ đào tạo bắt buộc.",
     "{U} yêu cầu tối thiểu bao nhiêu giờ đào tạo mỗi năm?", "{n} giờ",
     {"n": ["16", "20", "24", "32", "40"]}),
    ("tu_xa", "Nhân viên {u} được làm việc từ xa tối đa {n} ngày mỗi tuần.",
     "Một tuần nhân viên {u} được làm ở nhà mấy ngày?", "{n} ngày mỗi tuần",
     {"n": ["1", "2", "3"]}),
]  # fmt: skip


def vi_unit(index: int) -> str:
    """The unit of Vietnamese document ``index`` (a multiple of 5): its team and a region,
    so no two Vietnamese documents share one."""
    team = TEAMS[index % len(TEAMS)]
    return f"khối {VI_TEAMS[team]} {VI_REGIONS[(index // 5) // len(TEAMS)]}"


def vi_facts(index: int, count: int = 3) -> List[Dict[str, str]]:
    """The facts Vietnamese document ``index`` states: ``name``, ``statement`` (a sentence
    of the document), ``question``, ``answer``. Seeded by the index alone."""
    rng = random.Random(10_000 + index)
    unit = vi_unit(index)
    out = []
    for name, statement, question, answer, slots in rng.sample(VI_FACTS, count):
        values = {k: rng.choice(v) for k, v in slots.items()}
        values.update(u=unit, U=unit[0].upper() + unit[1:])
        out.append({"name": name, "statement": statement.format(**values),
                    "question": question.format(**values), "answer": answer.format(**values)})  # fmt: skip
    return out


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
    if vi:
        blocks = _with_facts(blocks, index)
    return title, blocks, vi


def _with_facts(blocks: List[Block], index: int) -> List[Block]:
    """Each fact of :func:`vi_facts` as its own paragraph, after a paragraph of the body."""
    rng = random.Random(20_000 + index)
    out = list(blocks)
    for fact in vi_facts(index):
        paragraphs = [i for i, (kind, _) in enumerate(out) if kind == "p"]
        out.insert(rng.choice(paragraphs) + 1, ("p", fact["statement"]))
    return out


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


def corpus_plan(n_docs: int = 200) -> List[str]:
    """The file names of :func:`corpus`, in order (the format mix scales with n)."""
    plan: List[str] = []
    total = sum(k for _, k in FORMATS)
    for ext, k in FORMATS:
        plan += [ext] * round(k * n_docs / total)
    plan = (plan + [".md"] * n_docs)[:n_docs]
    return [f"doc_{i:03d}{ext}" for i, ext in enumerate(plan)]


def corpus(n_docs: int = 200, seed: int = 2026) -> Dict[str, bytes]:
    """``file name -> bytes`` for ``n_docs`` documents (the format mix scales with n)."""
    rng = random.Random(seed)
    out: Dict[str, bytes] = {}
    for i, name in enumerate(corpus_plan(n_docs)):
        ext = "." + name.rsplit(".", 1)[1]
        title, blocks, _ = document(rng, i)
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


def vi_cases(n_docs: int = 200, collection: str = "corpus_vi", k: int = 20) -> List[Dict]:
    """The Vietnamese eval cases of :func:`corpus`: one per fact of a Vietnamese document.

    Each case is an operonx dataset row whose label is the fact sentence, by document
    key (the file name) and quote, so it resolves to a span in whatever version the
    pipeline makes of the file.
    """
    names = list(corpus_plan(n_docs))
    cases = []
    for index, name in enumerate(names):
        if index % 5:
            continue
        for fact in vi_facts(index):
            cases.append({
                "id": f"cvi-{index:03d}-{fact['name']}",
                "input": {"query": fact["question"], "collection": collection, "k": k},
                "expected": {"relevant": [{"doc_key": name, "quote": fact["statement"]}],
                             "answer": fact["answer"]},
                "tags": ["vi", fact["name"], name.rsplit(".", 1)[1]],
            })  # fmt: skip
    return cases


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--docs", type=int, default=200)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, data in corpus(args.docs).items():
        (args.out / name).write_bytes(data)
