"""In-memory store of record — dict-backed, no infrastructure.

The doc-store counterpart to the FAISS vector store: it needs no server,
so tests and examples can exercise the full two-store pipeline
(``VectorSearchOp`` → ``DocFetchOp``) without docker.

Not for production — the data lives in the process and dies with it.
Point ``doc_store`` at Postgres for anything real.
"""

from typing import Any, Dict, List, Optional, Sequence

from operonx.providers.doc_stores.base import BaseDocStore
from operonx.providers.doc_stores.config import DocStoreConfig

__all__ = ["MemoryDocStore"]


class MemoryDocStore(BaseDocStore):
    """Dict-backed document store.

    Documents are held per collection. Seed them with :meth:`put`, or
    pass ``documents=`` in the resource config.
    """

    __slots__ = ("config", "_collections")

    # Pure dict lookups — no I/O to wait on.
    bound = "cpu"

    def __init__(self, config: DocStoreConfig):
        self.config = config
        self._collections: Dict[str, Dict[Any, Dict[str, Any]]] = {}

        default = config.collection or "default"
        seeded = config.documents or []
        if seeded:
            self.put(seeded, collection=default)
        else:
            self._collections.setdefault(default, {})

    def put(
        self,
        documents: Sequence[Dict[str, Any]],
        collection: Optional[str] = None,
        id_field: Optional[str] = None,
    ) -> None:
        """Insert or replace documents, keyed by ``id_field``.

        Args:
            documents: Records to store. Each must carry ``id_field``.
            collection: Target collection; ``None`` uses the default.
            id_field: Key field; ``None`` uses the configured default.

        Raises:
            ValueError: If a document is missing its key field — without
                it the record could never be fetched back.
        """
        name = collection or self.config.collection or "default"
        key = id_field or self.config.id_field
        bucket = self._collections.setdefault(name, {})
        for doc in documents:
            if key not in doc:
                raise ValueError(
                    f"Document is missing its id field {key!r} and could never be "
                    f"fetched back: {doc!r}"
                )
            bucket[doc[key]] = dict(doc)

    async def _fetch(
        self,
        ids: Sequence[Any],
        collection: Optional[str] = None,
        fields: Optional[Sequence[str]] = None,
        id_field: str = "id",
    ) -> List[Dict[str, Any]]:
        """Look up ids in the chosen collection.

        Order is irrelevant here — ``BaseDocStore.fetch`` restores it —
        but the id field is always projected so the base can match rows
        back to the requested ids.
        """
        name = collection or self.config.collection or "default"
        bucket = self._collections.get(name)
        if bucket is None:
            known = ", ".join(sorted(self._collections)) or "(none)"
            raise ValueError(f"MemoryDocStore has no collection {name!r}. Seeded: {known}.")

        rows = []
        for id_ in ids:
            doc = bucket.get(id_)
            if doc is None:
                continue
            if fields:
                projected = {f: doc[f] for f in fields if f in doc}
                projected[id_field] = doc.get(id_field, id_)
                rows.append(projected)
            else:
                rows.append(dict(doc))
        return rows
