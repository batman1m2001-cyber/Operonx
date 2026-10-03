"""K1 gate (a): every golden document parses, satisfies the span invariant, and
matches its element-tree snapshot; both chunkers produce valid spans on it."""

import importlib.util
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
        assert make(None) == (DOCS / name).read_bytes(), f"{name} differs from its generator"
