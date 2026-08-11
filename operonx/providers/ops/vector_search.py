"""VectorSearchOp — vector similarity search over a derived index.

Returns ids, scores, and filterable metadata. It does **not** return
document content: the index is a derived index, not a store of record.
Hydrate content with ``DocFetchOp`` against your primary database. See
``OP_TAXONOMY_REFACTOR_PLAN.md`` §5.1.
"""

from typing import Any, Dict, Optional, Union

from operonx.core.configs import OpType
from operonx.core.ops import BaseOp
from operonx.core.ops.base import shorthand, split_shorthand_kwargs
from operonx.core.utils.common import Param
from operonx.providers.ops._utils import resolve_hub

__all__ = ["VectorSearchOp"]


class VectorSearchOp(BaseOp):
    """Op that runs vector similarity search via ResourceHub.

    Inputs:
        query_vector (list[float]): Query embedding. Required.
        top_k (int): Number of hits. Default 10.
        filter (dict | str): **Backend-native** metadata filter — dict
            for most backends, an expression string for Milvus. Never
            translated by operonx; each backend validates its own dialect
            and raises on shapes it doesn't recognise. Default None.
        collection (str): Collection / table / index to search. Default
            None, meaning the resource's configured default.

    Outputs:
        ids (list): Hit ids, best match first.
        scores (list[float]): Similarity per hit, index-aligned with ids.
        metadata (list[dict]): Indexed filterable fields per hit,
            index-aligned. ``{}`` for backends that store none.

    Example::

        hits = VectorSearchOp.of(
            resource="docs",
            query_vector=emb["embeddings"][0],
            top_k=20,
            filter={"tenant": "acme"},
        )
        docs = DocFetchOp.of(resource="main", ids=hits["ids"], collection="docs")
    """

    __slots__ = ["resource", "backend", "_initialized"]

    type: OpType = "vector-search"

    def __init__(
        self,
        resource: Optional[str] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs: Any,
    ):
        """Initialize VectorSearchOp.

        Args:
            resource: Resource key for the vector store. A bare name is
                looked up as ``vector_store:{resource}``; a key that
                already contains ``:`` is used verbatim.
            inputs: Input variable mappings.
            outputs: Output variable mappings.
            **kwargs: Additional keyword arguments for BaseOp.
        """
        # Networked stores dominate, so I/O is the right default. FAISS and
        # other in-process indices override this from their `bound` class
        # attribute once the resource resolves — see _ensure_initialized.
        kwargs.setdefault("bound", "io")
        super().__init__(**kwargs)

        self.resource = resource

        input_schema = {
            "query_vector": Param(type=list, required=True),
            "top_k": Param(type=int, required=False, default=10),
            "filter": Param(type=(dict, str), required=False, default=None),
            "collection": Param(type=str, required=False, default=None),
        }
        output_schema = {
            "ids": Param(type=list, required=True),
            "scores": Param(type=list, required=True),
            "metadata": Param(type=list, required=False),
        }

        self.inputs = self._merge_params(input_schema, inputs)
        self.outputs = self._merge_params(output_schema, outputs)

        self.backend = None
        self._initialized = False
        self._set_core(self._process)

    def warmup(self) -> None:
        """Eagerly resolve the backend on engine startup."""
        self._ensure_initialized()

    def _ensure_initialized(self):
        """Lazy-resolve the backend from ResourceHub on first use.

        Also adopts the backend's ``bound`` hint: a local FAISS index is
        CPU-bound while networked stores are I/O-bound, and the op can't
        know which it got until the resource resolves.
        """
        if self._initialized:
            return
        hub = resolve_hub()
        key = self.resource if ":" in (self.resource or "") else f"vector_store:{self.resource}"
        self.backend = hub.get(key)
        backend_bound = getattr(self.backend, "bound", None)
        if backend_bound:
            self.bound = backend_bound
        self._initialized = True

    async def _process(
        self,
        query_vector: list,
        top_k: int = 10,
        filter: Optional[Union[dict, str]] = None,
        collection: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the search and return index-aligned ids / scores / metadata."""
        self._ensure_initialized()
        ids, scores, metadata = await self.backend.search(
            query_vector=query_vector,
            top_k=top_k,
            filter=filter,
            collection=collection,
        )
        return {"ids": ids, "scores": scores, "metadata": metadata}

    @shorthand
    def of(cls, resource=None, **kwargs) -> "VectorSearchOp":
        """Create a VectorSearchOp with flat kwargs.

        Example::

            hits = VectorSearchOp.of(resource="docs", query_vector=emb["embeddings"][0])
        """
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        return cls(resource=resource, inputs=input_mappings or None, **init_kwargs)

    def serialize(self) -> dict:
        """Serialize for the Rust backend, including resource config."""
        self._ensure_initialized()
        base = super().serialize()
        base["resource"] = self.resource
        if self.backend and hasattr(self.backend, "config"):
            base["resource_config"] = self.backend.config.model_dump(mode="json")
        return base

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return vector-search-specific metadata."""
        meta: Dict[str, Any] = {"store": self.resource}
        if self.backend and hasattr(self.backend, "config"):
            cfg = self.backend.config
            meta["backend"] = getattr(cfg.api_type, "value", cfg.api_type)
            meta["metric"] = getattr(cfg.metric, "value", cfg.metric)
        return meta
