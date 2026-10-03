"""Quote-anchored labels resolved to spans in the active version (track5 §15.3).

A dataset names relevant text as ``{"doc_key": …, "quote": …}``. At eval time
the quote is found in the canonical text of the document's **active** version,
every occurrence of it, whitespace-insensitively, so a label written once keeps
working after a re-parse or a re-chunk. A document that is gone, or a quote it
no longer contains, raises: a label that silently resolves to nothing would
score every system 0 and look like a retrieval failure.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from operonx_kb.errors import KBError
from operonx_kb.eval.metrics import Occurrence
from operonx_kb.model.ids import document_id
from operonx_kb.ops._resources import blobs_of, catalog_of

__all__ = ["LabelError", "LabelResolver", "occurrences"]


class LabelError(KBError):
    """A dataset label does not resolve in the collection (stale or wrong)."""


def occurrences(canonical: str, quote: str) -> List[Tuple[int, int]]:
    """Every span of ``canonical`` holding ``quote``, whitespace-insensitively."""
    words = unicodedata.normalize("NFC", quote).split()
    if not words:
        return []
    pattern = re.compile(r"\s+".join(re.escape(w) for w in words))
    return [(m.start(), m.end()) for m in pattern.finditer(canonical)]


class LabelResolver:
    """Resolves labels through a catalog and blob store (resource keys), with a cache.

    Args:
        catalog: ``kb_catalog`` resource key.
        blobs: ``kb_blob`` resource key.
    """

    def __init__(self, catalog: str = "kb_catalog:main", blobs: str = "kb_blob:main"):
        self.catalog = catalog
        self.blobs = blobs
        self._texts: Dict[str, Tuple[str, str]] = {}

    def _active(self, collection: str, key: str) -> Tuple[str, str]:
        doc_id = document_id(collection, key)
        if doc_id not in self._texts:
            cat = catalog_of(self.catalog)
            doc = cat.get_document(doc_id)
            if doc is None or doc.active_version_id is None:
                raise LabelError(
                    f"label names {key!r}, which collection {collection!r} does not hold"
                )
            version = cat.get_version(doc.active_version_id)
            data = blobs_of(self.blobs).get(version.text_sha)
            if data is None:
                raise LabelError(f"the canonical text of {key!r} is missing from the blob store")
            self._texts[doc_id] = (version.id, data.decode("utf-8"))
        return self._texts[doc_id]

    def resolve(
        self, collection: str, relevant: Sequence[Mapping[str, Any]]
    ) -> List[List[Occurrence]]:
        """One list of ``(version_id, span)`` per label.

        Raises:
            LabelError: A document is not in the collection, or its text lacks the quote.
        """
        out = []
        for label in relevant:
            version, text = self._active(collection, label["doc_key"])
            spans = occurrences(text, label["quote"])
            if not spans:
                raise LabelError(
                    f"quote not found in {label['doc_key']!r}; the label is stale or wrong",
                    {"quote": label["quote"]},
                )
            out.append([(version, s) for s in spans])
        return out

    def clear(self) -> None:
        """Forget cached texts (after re-ingesting the collection)."""
        self._texts.clear()
