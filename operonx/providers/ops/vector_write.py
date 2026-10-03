"""VectorUpsertOp and VectorDeleteOp — the write half of the retrieval pair.

An index is derived from a store of record, so it has to follow that
store: vectors written when a document arrives, removed when it goes.
Without a delete, a document removed upstream stays findable here. See
``OP_TAXONOMY_REFACTOR_PLAN.md`` §5.7 — these ship now that a knowledge
base keeps an index in step with its catalog.

Both resolve a ``vector_store:`` resource the way ``VectorSearchOp``
does, and adopt the backend's ``bound``.
"""

from typing import Any, Dict, List, Optional, Union

from operonx.core.configs import OpType
from operonx.core.utils.common import Param
from operonx.providers.ops.vector_search import VectorStoreOpBase

__all__ = ["VectorUpsertOp", "VectorDeleteOp"]


class VectorUpsertOp(VectorStoreOpBase):
    """Op that inserts or replaces vectors by id via ResourceHub.

    Inputs:
        ids (list): Primary keys, one per vector. Required. Writing an id
            that is already there replaces its vector and metadata.
        vectors (list[list[float]]): Embeddings, index-aligned with
            ``ids``. Required — typically ``EmbeddingOp``'s
            ``embeddings``.
        metadata (list[dict]): Filterable fields, one dict per vector.
            Default None. Keep document content out: it belongs in the
            store of record. FAISS stores no metadata and ignores it;
            pgvector and Qdrant raise on a key outside
            ``metadata_columns``.
        collection (str): Collection / table / index. Default None,
            meaning the resource's configured default.

    Outputs:
        upserted (int): How many vectors were written.

    Example::

        emb = EmbeddingOp.of(resource="bge-m3", texts=chunks["texts"])
        write = VectorUpsertOp.of(
            resource="docs",
            ids=chunks["ids"],
            vectors=emb["embeddings"],
            metadata=chunks["tags"],
        )
    """

    show_keys_default = ("upserted",)

    __slots__ = []

    type: OpType = "vector-upsert"

    def _ports(self):
        return (
            {
                "ids": Param(type=list, required=True),
                "vectors": Param(type=list, required=True),
                "metadata": Param(type=list, required=False, default=None),
                "collection": Param(type=str, required=False, default=None),
            },
            {"upserted": Param(type=int, required=True)},
        )

    async def _process(
        self,
        ids: list,
        vectors: list,
        metadata: Optional[List[Dict[str, Any]]] = None,
        collection: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write the vectors; return how many."""
        self._ensure_initialized()
        await self.backend.upsert(
            ids=ids,
            vectors=vectors,
            metadata=metadata,
            collection=collection,
        )
        return {"upserted": len(ids)}


class VectorDeleteOp(VectorStoreOpBase):
    """Op that removes vectors by id, or by a filter, via ResourceHub.

    Inputs:
        ids (list): Primary keys to remove. Default None.
        filter (dict | str): **Backend-native** filter, the dialect
            ``VectorSearchOp`` takes; removes every vector it matches.
            Default None. FAISS cannot filter and raises.
        collection (str): Collection / table / index. Default None,
            meaning the resource's configured default.

    Exactly one of ``ids`` and ``filter``: neither, both, or an empty
    filter raise, because each could otherwise read as "delete
    everything". ``ids=[]`` removes nothing, and ids the index does not
    hold are not an error, so a cleanup pass can run again after a crash.

    Outputs:
        deleted (int | None): How many vectors were removed; None when
            the backend does not report it (Qdrant).

    Example::

        gone = VectorDeleteOp.of(resource="docs", ids=diff["removed"])
        purge = VectorDeleteOp.of(resource="docs", filter={"document_id": doc_id})
    """

    show_keys_default = ("deleted",)

    __slots__ = []

    type: OpType = "vector-delete"

    def _ports(self):
        return (
            {
                "ids": Param(type=list, required=False, default=None),
                "filter": Param(type=(dict, str), required=False, default=None),
                "collection": Param(type=str, required=False, default=None),
            },
            {"deleted": Param(type=int, required=False)},
        )

    async def _process(
        self,
        ids: Optional[list] = None,
        filter: Optional[Union[dict, str]] = None,
        collection: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Remove what ``ids`` or ``filter`` select; return how many."""
        self._ensure_initialized()
        deleted = await self.backend.delete(ids=ids, filter=filter, collection=collection)
        return {"deleted": deleted}
