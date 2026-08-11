"""FAISS vector store backend.

FAISS is an in-process index library, not a server — so this backend is
CPU-bound (``bound = "cpu"``) and requires no infrastructure, which
makes it the natural choice for tests and local development.

It also holds *only* vectors and ids: no metadata, no filtering. That
makes it a faithful check on the ids-only contract in plan §5.2 — if a
pipeline works against FAISS, it isn't secretly leaning on payload
storage.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from operonx.core.loggings import LOGGER
from operonx.providers.vector_stores.base import BaseVectorStore
from operonx.providers.vector_stores.config import VectorStoreConfig, VectorStoreMetric

__all__ = ["FaissVectorStore"]


class FaissVectorStore(BaseVectorStore):
    """Local FAISS index.

    Configure either a persisted index (``path=``, or ``collections=``
    for several), or an empty in-memory index (``dim=``) you populate
    with :meth:`upsert`.
    """

    __slots__ = ("config", "_indices", "_default")

    bound = "cpu"

    def __init__(self, config: VectorStoreConfig):
        import faiss  # noqa: F401 — fail fast with the factory's install hint

        self.config = config
        self._indices: Dict[str, Any] = {}

        if config.collections:
            for name, path in config.collections.items():
                self._indices[name] = self._load(path)
            self._default = config.collection or next(iter(config.collections))
        elif config.path:
            self._default = config.collection or "default"
            self._indices[self._default] = self._load(config.path)
        else:
            if not config.dim:
                raise ValueError(
                    "FaissVectorStore needs one of: path= (persisted index), "
                    "collections= ({name: path}), or dim= (empty in-memory index)."
                )
            self._default = config.collection or "default"
            self._indices[self._default] = self._new_index(config.dim)

    # ── index construction ────────────────────────────────────────────

    def _new_index(self, dim: int):
        """Build an empty id-mapped flat index matching the config metric."""
        import faiss

        if self.config.metric == VectorStoreMetric.L2:
            base = faiss.IndexFlatL2(dim)
        else:
            # IP over L2-normalised vectors is cosine similarity, so both
            # IP and COSINE map onto the same index type; COSINE just
            # normalises on the way in (see _prepare).
            base = faiss.IndexFlatIP(dim)
        return faiss.IndexIDMap2(base)

    @staticmethod
    def _load(path: str):
        import faiss

        return faiss.read_index(path)

    def _prepare(self, vectors: Sequence[Sequence[float]]) -> np.ndarray:
        """Coerce to float32 2-D, normalising when the metric is cosine."""
        import faiss

        arr = np.asarray(vectors, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        if self.config.metric == VectorStoreMetric.COSINE:
            arr = arr.copy()  # normalize_L2 mutates in place
            faiss.normalize_L2(arr)
        return arr

    def _index_for(self, collection: Optional[str]):
        name = collection or self._default
        if name not in self._indices:
            known = ", ".join(sorted(self._indices)) or "(none)"
            raise ValueError(
                f"FaissVectorStore has no collection {name!r}. Configured: {known}. "
                "Add it via collections={name: path} in resources.yaml."
            )
        return self._indices[name]

    # ── BaseVectorStore ───────────────────────────────────────────────

    async def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 10,
        filter: Optional[Union[Dict[str, Any], str]] = None,
        collection: Optional[str] = None,
    ) -> Tuple[List[Any], List[float], List[Dict[str, Any]]]:
        """Search the selected index.

        Raises:
            ValueError: If ``filter`` is anything but None — FAISS stores
                no metadata to filter on. Post-filtering in Python is
                deliberately not offered: over-fetching then filtering
                silently returns fewer than ``top_k`` hits, and a silent
                wrong answer is worse than a refusal.
        """
        if filter is not None:
            raise ValueError(
                "FAISS does not support metadata filtering. Its index holds vectors "
                "only. Use pgvector or Qdrant for filtered search, or pre-partition "
                "into separate FAISS indices and select via collection=."
            )

        index = self._index_for(collection)
        if index.ntotal == 0:
            return [], [], []

        query = self._prepare(query_vector)
        scores, ids = index.search(query, min(top_k, index.ntotal))

        out_ids: List[Any] = []
        out_scores: List[float] = []
        for raw_id, score in zip(ids[0], scores[0]):
            # FAISS pads with -1 when fewer than k neighbours exist.
            if raw_id == -1:
                continue
            out_ids.append(int(raw_id))
            out_scores.append(float(score))

        # FAISS holds no metadata; the contract still requires an aligned list.
        return out_ids, out_scores, [{} for _ in out_ids]

    async def upsert(
        self,
        ids: Sequence[Any],
        vectors: Sequence[Sequence[float]],
        metadata: Optional[Sequence[Dict[str, Any]]] = None,
        collection: Optional[str] = None,
    ) -> None:
        """Insert or replace vectors by id.

        FAISS ids are int64. ``metadata`` is accepted for contract
        parity and ignored — FAISS has nowhere to put it.
        """
        if metadata:
            LOGGER.debug(
                "FaissVectorStore.upsert ignoring metadata for %d vectors — "
                "FAISS stores none. Keep filterable fields in your store of record.",
                len(list(ids)),
            )

        index = self._index_for(collection)
        arr = self._prepare(vectors)
        id_arr = np.asarray(ids, dtype=np.int64)
        if len(id_arr) != len(arr):
            raise ValueError(f"ids/vectors length mismatch: {len(id_arr)} vs {len(arr)}")

        # IndexIDMap2 raises on duplicate ids, so replace means remove-then-add.
        try:
            index.remove_ids(id_arr)
        except Exception:  # noqa: BLE001 — index types differ in remove support
            pass
        index.add_with_ids(arr, id_arr)

    def save(self, path: str, collection: Optional[str] = None) -> None:
        """Persist an index to disk. Not part of the ABC — FAISS-specific."""
        import faiss

        faiss.write_index(self._index_for(collection), path)
