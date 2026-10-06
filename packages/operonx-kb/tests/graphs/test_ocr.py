"""OCR for scanned PDF pages (CollectionSpec.ocr), through the real ingest and search graphs.

The scan is made here: a Vietnamese page is typeset (reportlab, DejaVu), rendered to an
image (pypdfium2) and saved as an image-only PDF, so the page has no text layer at all.
"""

import asyncio
import shutil
from pathlib import Path

import pytest

pytest.importorskip("pypdfium2")
pytest.importorskip("docling_parse")
pytest.importorskip("reportlab")
pytestmark = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="needs the tesseract binary"
)

FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
LINES = [
    "Quy định về nghỉ phép năm",
    "Mỗi nhân viên được nghỉ phép mười hai ngày mỗi năm.",
    "Ngày nghỉ phép được đăng ký trên cổng thông tin nhân sự.",
]


def run(coro):
    return asyncio.run(coro)


def scanned_pdf(path: Path) -> Path:
    import pypdfium2
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    if "DejaVu" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVu", str(FONT)))
    typed = path.with_suffix(".typed.pdf")
    c = canvas.Canvas(str(typed), pagesize=(595, 842), invariant=1)
    c.setFont("DejaVu", 18)
    c.drawString(72, 760, LINES[0])
    c.setFont("DejaVu", 12)
    for i, line in enumerate(LINES[1:]):
        c.drawString(72, 720 - 22 * i, line)
    c.save()
    image = pypdfium2.PdfDocument(str(typed))[0].render(scale=200 / 72).to_pil().convert("RGB")
    image.save(path, "PDF", resolution=200)
    return path


@pytest.fixture
def scans(kbx, tmp_path):
    from operonx_kb import ChunkerSpec, CollectionSpec, DenseIndexSpec, OcrSpec
    from operonx_kb.model.collection import LexicalIndexSpec

    kbx.create_collection(
        "scans",
        CollectionSpec(
            chunker=ChunkerSpec(max_tokens=120, min_tokens=8),
            dense=DenseIndexSpec(embedder="hash", store="vector_store:kb"),
            lexical=LexicalIndexSpec(collection="lex_scans"),
            ocr=OcrSpec(languages="vie"),
        ),
    )
    kbx.pdf = scanned_pdf(tmp_path / "policy.pdf")
    return kbx


def test_without_ocr_a_scan_has_no_text(scans):
    from operonx_kb.pdf.backend import DoclingParseBackend

    (page,) = DoclingParseBackend().pages(scans.pdf.read_bytes())
    assert page.words == []  # the fixture really is a scan


def test_ocr_reads_a_scanned_page_into_searchable_cited_text(scans):
    got = run(scans.add("scans", str(scans.pdf), key="policy.pdf"))
    assert got["action"] != "skip"
    out = run(scans.search("scans", "nghỉ phép mười hai ngày", mode="lexical", k=3))
    assert out["hits"], "the OCR'd text is indexed"
    text = " ".join(h["text"] for h in out["hits"])
    assert "mười hai ngày" in text and "nghỉ phép" in text
    hit = out["hits"][0]
    assert hit["pages"] == [1]
    (doc,) = [d for d in scans.catalog.list_documents("scans") if d.key == "policy.pdf"]
    (page,) = scans.catalog.pages(doc.active_version_id)
    assert page.text_layer is False  # the catalog says where the text came from


def test_the_ocr_settings_are_in_the_pipeline_fingerprint(scans):
    from operonx_kb import CollectionSpec, OcrSpec
    from operonx_kb.pipeline import Pipeline

    data = scans.pdf.read_bytes()
    plain = Pipeline(CollectionSpec())
    vie = Pipeline(CollectionSpec(ocr=OcrSpec(languages="vie")))
    eng = Pipeline(CollectionSpec(ocr=OcrSpec(languages="eng")))
    fps = {p.fingerprint(p.parser_for(data, name="a.pdf")) for p in (plain, vie, eng)}
    assert len(fps) == 3  # turning OCR on, or changing its languages, re-parses
