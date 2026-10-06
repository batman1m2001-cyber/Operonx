"""Page images for the viewer: a PDF page rendered from the version's own bytes.

The catalog keeps no page images (``Page.image_sha`` stays empty: rendering
every page at ingest costs time and blob space for pages nobody opens). A page
is rendered on request from the raw bytes the version was parsed from, with the
renderer the ML layout uses (:class:`~operonx_kb.pdf.backend.PdfiumRenderer`),
so the image is the page the boxes were measured on. Rendering is
deterministic: the result is keyed by ``(raw_sha, page, scale)`` and kept in a
small in-process cache.
"""

from __future__ import annotations

import io
import threading
from collections import OrderedDict
from typing import Tuple

from operonx_kb.errors import CatalogError, MissingExtraError
from operonx_kb.kb import KnowledgeBase

__all__ = ["PageImages", "MIN_SCALE", "MAX_SCALE"]

#: Pixels per PDF point, the range a caller may ask for (1.0 is 72 dpi).
MIN_SCALE = 0.5
MAX_SCALE = 4.0


class PageImages:
    """PNG renders of PDF pages, the last ``capacity`` of them cached.

    Args:
        kb: Where versions and their raw bytes are.
        capacity: Rendered pages kept in memory.
    """

    def __init__(self, kb: KnowledgeBase, capacity: int = 64):
        self.kb = kb
        self.capacity = capacity
        self._cache: "OrderedDict[Tuple[str, int, float], bytes]" = OrderedDict()
        self._lock = threading.Lock()

    def png(self, version_id: str, page_no: int, scale: float = 1.5) -> bytes:
        """The page as PNG bytes.

        Raises:
            CatalogError: No such version, it is not a PDF, it has no such page, or
                its raw bytes are gone from the blob store.
            ValueError: ``scale`` is outside ``[MIN_SCALE, MAX_SCALE]``.
            MissingExtraError: pypdfium2 or Pillow is not installed (``admin`` extra).
        """
        if not MIN_SCALE <= scale <= MAX_SCALE:
            raise ValueError(f"scale is {MIN_SCALE} to {MAX_SCALE} pixels per point, not {scale}")
        version = self.kb.catalog.get_version(version_id)
        if version is None:
            raise CatalogError(f"no version {version_id!r}")
        document = self.kb.catalog.get_document(version.document_id)
        if document is None or document.mime != "application/pdf":
            raise CatalogError(
                f"version {version_id} is a {document.mime if document else 'missing'} "
                "document: only PDF pages have images; show its text instead"
            )
        pages = len(self.kb.catalog.pages(version_id))
        if not 1 <= page_no <= pages:
            raise CatalogError(
                f"version {version_id} has no page {page_no}; its pages are 1 to {pages}"
            )
        key = (version.raw_sha, page_no, round(scale, 3))
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        data = self.kb.blobs.get(version.raw_sha)
        if data is None:
            raise CatalogError(
                f"the PDF of version {version_id} is missing from the blob store",
                {"sha": version.raw_sha},
            )
        png = _render(data, page_no, scale)
        with self._lock:
            self._cache[key] = png
            while len(self._cache) > self.capacity:
                self._cache.popitem(last=False)
        return png


def _render(data: bytes, page_no: int, scale: float) -> bytes:
    try:
        import PIL  # noqa: F401 — PdfiumRenderer hands back a Pillow image
        import pypdfium2  # noqa: F401
    except ImportError as exc:
        raise MissingExtraError("Page images", "admin", exc) from exc
    from operonx_kb.pdf.backend import PdfiumRenderer

    with PdfiumRenderer(data) as renderer:
        image = renderer.render(page_no, scale)
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=False)
    return out.getvalue()
