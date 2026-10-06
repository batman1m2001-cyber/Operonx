"""OCR for scanned pages: a :class:`PdfBackend` that reads a page with no text layer
from its image.

:class:`OcrBackend` wraps the text backend (docling-parse). A page that comes back
with fewer than ``min_words`` words — a scan, a photo of a page — is rendered
(pypdfium2) and read by an :class:`OcrEngine` into the same :class:`Word`\\ s, with
their boxes in points, so layout, reading order, chunking and citations work on it
unchanged; the page is marked ``ocr`` (``text_layer=False`` in the catalog). Pages
with a text layer are never OCR'd.

:class:`TesseractEngine` runs the ``tesseract`` binary (Apache-2.0; not a Python
dependency: ``apt install tesseract-ocr tesseract-ocr-vie``) on each page image
and reads its TSV: one row per word, with its box and confidence.
"""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from operonx_kb.errors import KBError
from operonx_kb.pdf.backend import PdfBackend, PdfPage, Word

__all__ = ["OcrBackend", "OcrEngine", "TesseractEngine", "OcrUnavailable"]


class OcrUnavailable(KBError):
    """The OCR engine cannot run here (its binary or language data is missing)."""


class OcrEngine(ABC):
    """Reads the words of one page image."""

    name: str = ""

    def config(self) -> Dict[str, Any]:
        return {}

    @abstractmethod
    def words(self, image: Any, scale: float) -> List[Word]:
        """The words of ``image`` (a PIL image rendered at ``scale`` × 72 dpi), their
        boxes in points (pixels / ``scale``)."""


class TesseractEngine(OcrEngine):
    """The ``tesseract`` binary.

    Args:
        languages: Tesseract language codes joined by ``+`` (``"vie+eng"``).
        min_confidence: Words read below this confidence (0–100) are dropped.
        binary: The executable; default ``tesseract`` on ``PATH``.
    """

    name = "tesseract"

    def __init__(self, languages: str = "vie+eng", min_confidence: float = 30.0,
                 binary: str = "tesseract") -> None:  # fmt: skip
        self.languages = languages
        self.min_confidence = float(min_confidence)
        self.binary = binary

    def _check(self) -> str:
        path = shutil.which(self.binary)
        if path is None:
            raise OcrUnavailable(
                f"OCR needs the {self.binary!r} binary: apt install tesseract-ocr "
                "tesseract-ocr-vie (or set OcrSpec.binary)"
            )
        return path

    def version(self) -> Optional[str]:
        path = shutil.which(self.binary)
        if path is None:
            return None
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30)
        first = (out.stdout or out.stderr).splitlines()
        return first[0].strip() if first else None

    def config(self) -> Dict[str, Any]:
        # the engine's version is part of the fingerprint: an upgrade re-reads the scans
        return {"languages": self.languages, "min_confidence": self.min_confidence,
                "version": self.version()}  # fmt: skip

    def words(self, image: Any, scale: float) -> List[Word]:
        path = self._check()
        png = io.BytesIO()
        image.save(png, format="PNG")
        run = subprocess.run(
            [path, "stdin", "stdout", "-l", self.languages, "--psm", "3", "tsv"],
            input=png.getvalue(),
            capture_output=True,
            timeout=300,
        )
        if run.returncode != 0:
            raise OcrUnavailable(
                f"tesseract failed ({run.returncode}): "
                f"{run.stderr.decode('utf-8', 'replace').strip()[:300]}"
            )
        rows = csv.DictReader(io.StringIO(run.stdout.decode("utf-8", "replace")), delimiter="\t",
                              quoting=csv.QUOTE_NONE)  # fmt: skip
        out: List[Word] = []
        for row in rows:
            text = (row.get("text") or "").strip()
            if row.get("level") != "5" or not text:
                continue
            if float(row.get("conf") or -1) < self.min_confidence:
                continue
            x, y = float(row["left"]) / scale, float(row["top"]) / scale
            w, h = float(row["width"]) / scale, float(row["height"]) / scale
            out.append(Word(text=text, x0=x, y0=y, x1=x + w, y1=y + h, size=h))
        return out


class OcrBackend(PdfBackend):
    """``inner``'s pages, with the ones that have no text read by ``engine``.

    Args:
        inner: The text backend (default docling-parse).
        engine: The OCR engine (default :class:`TesseractEngine`).
        dpi: Resolution the page is rendered at for OCR.
        min_words: A page with fewer words than this is OCR'd.
    """

    name = "ocr"
    version = "1"

    def __init__(self, inner: Optional[PdfBackend] = None, engine: Optional[OcrEngine] = None,
                 dpi: int = 200, min_words: int = 3) -> None:  # fmt: skip
        from operonx_kb.pdf.backend import DoclingParseBackend

        self.inner = inner or DoclingParseBackend()
        self.engine = engine or TesseractEngine()
        self.dpi = int(dpi)
        self.min_words = int(min_words)

    def config(self) -> Dict[str, Any]:
        return {"inner": self.inner.fingerprint(), "engine": self.engine.name,
                "engine_config": self.engine.config(), "dpi": self.dpi,
                "min_words": self.min_words}  # fmt: skip

    def renderer(self, data: bytes, password: Optional[str] = None):
        return self.inner.renderer(data, password)

    def pages(self, data: bytes, password: Optional[str] = None) -> List[PdfPage]:
        pages = self.inner.pages(data, password=password)
        scanned = [p for p in pages if len(p.words) < self.min_words]
        if not scanned:
            return pages
        scale = self.dpi / 72.0
        with self.inner.renderer(data, password=password) as renderer:
            for page in scanned:
                page.words = self.engine.words(renderer.render(page.page_no, scale=scale), scale)
                page.ocr = True
        return pages
