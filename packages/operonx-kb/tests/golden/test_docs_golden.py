"""K1 gate (a): every golden document parses, satisfies the span invariant, and
matches its element-tree snapshot; both chunkers produce valid spans on it."""

import base64
import importlib.util
import re
import zlib
from pathlib import Path

import pytest

from operonx_kb.chunking import RecursiveChunker, StructuralChunker, materialize
from operonx_kb.parsing.router import ParserRouter
from operonx_kb.structure.build import build_version
from operonx_kb.testing.golden import compare_or_update, tree_snapshot
from operonx_kb.text.spans import check_chunks, check_elements

GOLDEN = Path(__file__).parent
DOCS = GOLDEN / "docs"
FILES = sorted(p for p in DOCS.iterdir() if p.is_file())
HAS_PDF = importlib.util.find_spec("docling_parse") is not None


def _params():
    for p in FILES:
        marks = (
            [pytest.mark.pdf, pytest.mark.skipif(not HAS_PDF, reason="needs the 'pdf' extra")]
            if p.suffix == ".pdf"
            else []
        )
        yield pytest.param(p, id=p.name, marks=marks)


@pytest.mark.parametrize("path", list(_params()))
def test_golden_document(path: Path, update_golden):
    data = path.read_bytes()
    parsed = ParserRouter().for_file(data, name=path.name).parse(data, name=path.name)
    tree = build_version(parsed, "ver_golden")
    assert check_elements(tree.canonical, tree.elements) == len(tree.elements)
    for chunker in (StructuralChunker(max_tokens=120), RecursiveChunker(max_tokens=120)):
        chunks, occurrences = materialize(
            tree, chunker.draft(tree), document_id="doc_g", version_id="ver_golden", chunker=chunker
        )
        assert chunks, f"{chunker.name} produced no chunks"
        assert check_chunks(
            tree.canonical, occurrences, {c.id: c.content_sha for c in chunks}
        ) == len(chunks)
    compare_or_update(tree_snapshot(tree), GOLDEN / "expected" / f"{path.name}.json", update_golden)


def test_corpus_has_every_format():
    assert {p.suffix for p in FILES} >= {".txt", ".md", ".html", ".docx", ".pptx", ".xlsx", ".pdf"}


def _generators(module: str):
    spec = importlib.util.spec_from_file_location(module, GOLDEN / f"{module}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.GENERATORS


def test_office_generators_are_reproducible():
    GENERATORS = _generators("make_office")

    for name, make in GENERATORS.items():
        assert make(None) == (DOCS / name).read_bytes(), f"{name} differs from its generator"


@pytest.mark.skipif(
    not Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf").exists(),
    reason="needs DejaVu fonts",
)
@pytest.mark.skipif(
    importlib.util.find_spec("reportlab") is None, reason="needs reportlab (dev group)"
)
def test_pdf_generators_are_reproducible():
    GENERATORS = _generators("make_pdfs")

    for name, make in GENERATORS.items():
        made, kept = _pdf_streams(make(None)), _pdf_streams((DOCS / name).read_bytes())
        assert made == kept, f"{name} differs from its generator: {_first_difference(made, kept)}"


def _first_difference(made: list, kept: list) -> str:
    """Where two lists of streams part: which stream, at which byte, and the bytes around it."""
    if len(made) != len(kept):
        return f"{len(made)} streams made, {len(kept)} kept"
    for n, (a, b) in enumerate(zip(made, kept)):
        if a != b:
            at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            lo = max(0, at - 60)
            return (
                f"stream {n} at byte {at} (lengths {len(a)} and {len(b)}): "
                f"made {a[lo : at + 60]!r}, kept {b[lo : at + 60]!r}"
            )
    return "nothing"


def _pdf_streams(pdf: bytes) -> list:
    """Every stream of a PDF (page content, embedded fonts), inflated.

    The files are compared by what they hold, not by their bytes: reportlab deflates each stream
    with the zlib the interpreter was built with, and two zlibs can deflate the same content to a
    different length — one byte, on the CI image, which shifted every offset after it.
    """
    out = []
    for raw in re.findall(rb"stream\r?\n(.*?)endstream", pdf, flags=re.S):
        if raw.rstrip().endswith(b"~>"):  # a page's content: ASCII85 over deflate
            raw = base64.a85decode(raw.strip(), adobe=True)
        try:
            out.append(
                zlib.decompressobj().decompress(raw)
            )  # tolerant of a newline before `endstream`
        except zlib.error:
            out.append(raw)
    assert out, "no stream found: not a PDF this test can read"
    return out
