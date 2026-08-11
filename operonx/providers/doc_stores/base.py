"""Document store backend contract — the *store of record*.

A vector index is derived data; this is the source of truth. See
``OP_TAXONOMY_REFACTOR_PLAN.md`` §5.8.

**Scope is deliberately narrow: fetch by primary key, with an optional
projection. Nothing else.** No joins, no writes, no transactions, no
custom SQL, no query-in-YAML. Anything past that line is a bare ``@op``
against your own client — the backends here stay importable for exactly
that. Without the boundary written down, this grows an ORM one feature
request at a time.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx.providers.doc_stores._reorder import partition_by_ids

__all__ = ["BaseDocStore"]


class BaseDocStore(ABC):
    """Abstract base class for key-based document retrieval.

    Subclasses implement :meth:`_fetch`, which may return rows in any
    order. The concrete :meth:`fetch` restores ``ids`` order and reports
    missing ids — putting that in the base rather than in each backend
    means no backend can get the silent-misalignment bug wrong.

    Attributes:
        bound: Thread-pool hint read by ``DocFetchOp``. Networked stores
            are I/O-bound; an in-process store would override to ``cpu``.
    """

    __slots__ = []

    bound: str = "io"

    @abstractmethod
    async def _fetch(
        self,
        ids: Sequence[Any],
        collection: Optional[str] = None,
        fields: Optional[Sequence[str]] = None,
        id_field: str = "id",
    ) -> List[Dict[str, Any]]:
        """Fetch records whose key is in ``ids``.

        Order does not matter — :meth:`fetch` restores it. Rows MUST
        include ``id_field`` so they can be matched back, even when
        ``fields`` omits it.

        Args:
            ids: Primary keys to fetch.
            collection: Table / collection / key-prefix. ``None`` uses
                the configured default.
            fields: Projection. ``None`` selects everything.
            id_field: Name of the primary-key field.

        Returns:
            Matching records, any order.
        """

    async def fetch(
        self,
        ids: Sequence[Any],
        collection: Optional[str] = None,
        fields: Optional[Sequence[str]] = None,
        id_field: str = "id",
    ) -> Tuple[List[Dict[str, Any]], List[Any]]:
        """Fetch records **in ``ids`` order**, plus the ids that had none.

        Returns:
            ``(rows, missing)``. ``rows`` is index-aligned with the subset
            of ``ids`` that resolved; ``missing`` lists the rest.
        """
        if not ids:
            return [], []
        raw = await self._fetch(ids, collection=collection, fields=fields, id_field=id_field)
        return partition_by_ids(raw, ids, key=id_field)
