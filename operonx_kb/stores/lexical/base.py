"""The lexical index contract (PLAN R1, track5 §7.7).

A lexical index is a **derived index**, like a vector store: it holds the
analyzed text of chunks under the same int64 keys the dense index uses
(:func:`~operonx_kb.model.ids.vector_id`) plus the filter payload
(:func:`~operonx_kb.model.filter.index_payload`), never anything the catalog
does not also hold. The KB records every key it writes in the catalog's
ledger, so delete, GC, ``verify`` and rebuild treat it exactly like the dense
index, and dropping it loses nothing a rebuild cannot restore.

The shape follows operonx's ``BaseVectorStore``: ``search`` takes a filter in
the backend's **native** dialect, which the KB compiles from a ``KBFilter``
(:func:`operonx_kb.retrieval.filters.native_filter`); a backend never
interprets a ``KBFilter`` itself, and an unknown filter shape raises.

Tokens arrive analyzed (:class:`~operonx_kb.text.analyze.Analyzer`); a backend
stores and matches them as given.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from operonx_kb.model.filter import PAYLOAD_KEYS

__all__ = ["LexicalIndex", "table_name", "split_payload"]

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


def table_name(collection: Optional[str]) -> str:
    """The table of a lexical collection (``None`` is ``"default"``)."""
    name = collection or "default"
    if not _IDENT.match(name):
        raise ValueError(f"lexical collection {name!r} must be a lowercase SQL identifier")
    return f"kbx_lex_{name}"


def split_payload(payload: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``(fixed fields, declared fields without their kb_f_ prefix)`` of a payload.

    Raises:
        ValueError: A key is neither a fixed payload key nor a ``kb_f_`` field.
    """
    fixed = {k: payload.get(k) for k in PAYLOAD_KEYS}
    fields = {k[5:]: v for k, v in payload.items() if k.startswith("kb_f_")}
    unknown = set(payload) - set(PAYLOAD_KEYS) - {f"kb_f_{k}" for k in fields}
    if unknown:
        raise ValueError(f"payload keys {sorted(unknown)} are not part of the KB payload")
    return fixed, fields


class LexicalIndex(ABC):
    """A lexical index: analyzed tokens and filter payloads under int64 keys.

    Attributes:
        dialect: Which filter compiler produces this backend's native filters
            (``"sqlite"`` or ``"postgres"``).
    """

    dialect: str = ""

    @abstractmethod
    def upsert(
        self,
        ids: Sequence[int],
        tokens: Sequence[Sequence[str]],
        payloads: Sequence[Mapping[str, Any]],
        collection: Optional[str] = None,
    ) -> int:
        """Insert or replace entries by key; return how many were written.

        An empty batch writes nothing.
        """

    @abstractmethod
    def delete(self, ids: Sequence[int], collection: Optional[str] = None) -> int:
        """Remove entries by key; keys the index does not hold are not an error."""

    @abstractmethod
    def search(
        self,
        tokens: Sequence[str],
        top_k: int = 10,
        filter: Optional[Any] = None,
        collection: Optional[str] = None,
    ) -> Tuple[List[int], List[float]]:
        """The ``top_k`` best entries for the query tokens (any of them), best first.

        Args:
            tokens: Analyzed query tokens.
            filter: A native filter of this backend's dialect, or ``None``.

        Returns:
            ``(ids, scores)``, index-aligned; a higher score is better.
        """

    @abstractmethod
    def ids(self, collection: Optional[str] = None) -> Set[int]:
        """Every key the collection holds (unlike a vector store, an FTS table can list them)."""

    @abstractmethod
    def drop(self, collection: Optional[str] = None) -> None:
        """Delete the collection's table."""
