"""Errors raised by operonx-kb.

Every message says what went wrong and what to do about it, the way operonx
errors do. Integrity errors (a span that does not round-trip, a catalog write
that failed) are raised, never logged and swallowed: track5 principle 7,
"fail-loud integrity". Enrichment fails loud too (PLAN E4): a version is never
committed half enriched.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = [
    "KBError",
    "SpanInvariantError",
    "UnsupportedFormatError",
    "DocumentParseError",
    "CatalogError",
    "EnrichmentError",
    "FilterError",
    "MissingExtraError",
]


class KBError(Exception):
    """Base class for operonx-kb errors.

    Args:
        message: What went wrong, and how to fix it.
        context: Extra facts printed one per line under the message.
    """

    def __init__(self, message: str, context: Optional[Dict[str, Any]] = None):
        self.context = context or {}
        lines = [message]
        for key, value in self.context.items():
            text = repr(value)
            if len(text) > 200:
                text = text[:200] + "..."
            lines.append(f"  {key}: {text}")
        super().__init__("\n".join(lines))


class SpanInvariantError(KBError):
    """An element or chunk span does not reproduce its text from the canonical text.

    This is a bug in a parser, the structurer or the chunker, never bad input:
    every span is assigned by our own serializer. The version is not committed.
    """


class UnsupportedFormatError(KBError):
    """No parser handles this file type."""


class DocumentParseError(KBError):
    """A parser could not read a document (corrupt, encrypted, too large)."""


class CatalogError(KBError):
    """The catalog refused an operation (missing collection, conflicting version, schema)."""


class EnrichmentError(KBError):
    """A model answer an enrichment stage needs is missing or unusable (PLAN E4).

    The version is not committed; the answers that came back are cached, so the
    next ingest asks the model only for the rest.
    """


class FilterError(KBError, ValueError):
    """A ``KBFilter`` names a field the collection does not declare, holds a value of
    the wrong type, or cannot be applied by an index backend.

    A filter is never dropped or weakened to make a query run: that would read
    other tenants' documents (track5 §12.3).
    """


class MissingExtraError(KBError, ImportError):
    """An optional dependency is not installed.

    Args:
        feature: What needs it, e.g. ``"PDF parsing"``.
        extra: The extra that installs it, e.g. ``"pdf"``.
        original: The ``ImportError`` raised by the import.
    """

    def __init__(self, feature: str, extra: str, original: Optional[BaseException] = None):
        super().__init__(
            f"{feature} needs the '{extra}' extra.\n"
            f"  Install with: pip install 'operonx-kb[{extra}]'",
            {"original error": str(original)} if original else None,
        )
