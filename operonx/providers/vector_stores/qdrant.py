"""Qdrant vector store.

The standalone option for when you outgrow Postgres. Note that its
headline feature — arbitrary payload storage — is one this framework
tells you not to lean on: the index carries vectors, ids, and *filterable*
metadata, while document content stays in your store of record and is
hydrated by ``DocFetchOp``. See ``OP_TAXONOMY_REFACTOR_PLAN.md`` §5.1.

Collections are expected to exist already; creating them (and choosing
the distance function and vector size) is an ingestion-time concern::

    client.create_collection(
        collection_name="docs",
        vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE),
    )
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from operonx.providers.vector_stores.base import BaseVectorStore
from operonx.providers.vector_stores.config import VectorStoreConfig

__all__ = ["QdrantVectorStore"]


# Clients are cached per (url, api_key) so the HTTP/gRPC connection pool is
# reused across ops — building one per call would pay connection setup on
# every request, the same reason TritonClient and the pg pool are cached.
_clients: Dict[Tuple[Optional[str], Optional[str]], Any] = {}


def _get_client(url: Optional[str], api_key: Optional[str]):
    """Return the process-cached ``AsyncQdrantClient`` for these settings."""
    key = (url, api_key)
    if key not in _clients:
        try:
            from qdrant_client import AsyncQdrantClient
        except ImportError as e:  # pragma: no cover - exercised via factory
            raise ImportError(
                "QdrantVectorStore requires additional packages.\n"
                "  Install with: pip install operonx[qdrant]\n"
                f"  Original error: {e}"
            ) from e
        _clients[key] = AsyncQdrantClient(url=url, api_key=api_key)
    return _clients[key]


def _reset_client_cache() -> None:
    """Drop cached clients. Test-only helper."""
    _clients.clear()


class QdrantVectorStore(BaseVectorStore):
    """Vector similarity search backed by Qdrant."""

    __slots__ = ("config", "_client", "_default_collection", "_payload_keys")

    bound = "io"

    def __init__(self, config: VectorStoreConfig):
        if not config.url:
            raise ValueError("QdrantVectorStore requires url= in the resource config.")
        if not (config.collection or config.collections):
            raise ValueError(
                "QdrantVectorStore requires collection= in the resource config "
                "(or pass collection= per call)."
            )

        self.config = config
        self._client = _get_client(config.url, config.api_key)
        self._default_collection = config.collection

        # Restrict returned payload to declared filterable fields. Left
        # unset, Qdrant hands back the whole payload — which is how
        # document content sneaks into a derived index.
        self._payload_keys: Union[bool, List[str]] = (
            list(config.metadata_columns) if config.metadata_columns else True
        )

    def _collection(self, collection: Optional[str]) -> str:
        name = collection or self._default_collection
        if not name:
            raise ValueError(
                "QdrantVectorStore has no collection: pass collection= on the op "
                "or set collection: in the resource config."
            )
        return name

    @staticmethod
    def _to_filter(filter: Optional[Union[Dict[str, Any], str]]):
        """Convert a native Qdrant filter dict into ``models.Filter``.

        The dict is Qdrant's own condition-tree dialect (``must`` /
        ``should`` / ``must_not``), passed through untranslated. Pydantic
        validates it, so an unrecognised shape raises rather than being
        silently ignored — a filter that fails to apply returns *more*
        rows, which in a multi-tenant system is a data leak.
        """
        if filter is None:
            return None

        from qdrant_client import models

        if isinstance(filter, str):
            raise ValueError(
                "Qdrant filters must be a condition-tree dict, not a string. "
                'Use {"must": [{"key": "tenant", "match": {"value": "acme"}}]}. '
                "(Expression strings are Milvus's dialect, not Qdrant's.)"
            )
        if isinstance(filter, models.Filter):
            return filter
        if not isinstance(filter, dict):
            raise ValueError(f"Qdrant filters must be a dict, got {type(filter).__name__}.")

        try:
            return models.Filter(**filter)
        except Exception as e:
            raise ValueError(
                f"Invalid Qdrant filter: {e}. Expected a condition tree such as "
                '{"must": [{"key": "tenant", "match": {"value": "acme"}}]}. '
                "See operonx/providers/vector_stores/README.md for the dialect."
            ) from e

    # ── BaseVectorStore ───────────────────────────────────────────────

    async def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 10,
        filter: Optional[Union[Dict[str, Any], str]] = None,
        collection: Optional[str] = None,
    ) -> Tuple[List[Any], List[float], List[Dict[str, Any]]]:
        """Nearest-neighbour search.

        The distance function is fixed on the Qdrant collection, not per
        query, so ``config.metric`` is informational here — the
        collection governs. Qdrant always returns hits best-first, which
        is the contract regardless of whether its score is a similarity
        or a distance.
        """
        response = await self._client.query_points(
            collection_name=self._collection(collection),
            query=list(query_vector),
            limit=top_k,
            query_filter=self._to_filter(filter),
            with_payload=self._payload_keys,
        )

        ids: List[Any] = []
        scores: List[float] = []
        metadata: List[Dict[str, Any]] = []
        for point in response.points:
            ids.append(point.id)
            scores.append(float(point.score))
            metadata.append(dict(point.payload or {}))
        return ids, scores, metadata

    async def upsert(
        self,
        ids: Sequence[Any],
        vectors: Sequence[Sequence[float]],
        metadata: Optional[Sequence[Dict[str, Any]]] = None,
        collection: Optional[str] = None,
    ) -> None:
        """Insert or replace points by id.

        When ``metadata_columns`` is configured, payload keys outside it
        raise — silently dropping one means the filter depending on it
        stops matching later, with no error at write time.
        """
        from qdrant_client import models

        ids = list(ids)
        vectors = list(vectors)
        if len(ids) != len(vectors):
            raise ValueError(f"ids/vectors length mismatch: {len(ids)} vs {len(vectors)}")
        if metadata is not None and len(metadata) != len(ids):
            raise ValueError(f"ids/metadata length mismatch: {len(ids)} vs {len(metadata)}")

        declared = self.config.metadata_columns
        points = []
        for i, (point_id, vector) in enumerate(zip(ids, vectors)):
            payload = (metadata[i] if metadata else None) or {}
            if declared:
                unknown = set(payload) - set(declared)
                if unknown:
                    raise ValueError(
                        f"metadata keys {sorted(unknown)} are not in metadata_columns="
                        f"{list(declared)}. Add them to the resource config or drop "
                        "them from the payload."
                    )
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=[float(v) for v in vector],
                    payload=dict(payload),
                )
            )

        await self._client.upsert(
            collection_name=self._collection(collection),
            points=points,
        )
