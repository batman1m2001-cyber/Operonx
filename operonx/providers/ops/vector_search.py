"""VectorSearchOp — vector similarity search over a derived index.

Returns ids, scores, and filterable metadata. It does **not** return
document content: the index is a derived index, not a store of record.
Hydrate content with ``DocFetchOp`` against your primary database. See
``OP_TAXONOMY_REFACTOR_PLAN.md`` §5.1.

The write half — ``VectorUpsertOp`` and ``VectorDeleteOp`` — lives in
:mod:`operonx.providers.ops.vector_write` and shares this module's
:class:`VectorStoreOpBase`.
"""

from abc import abstractmethod
from typing import Any, Dict, Optional, Tuple, Union

from operonx.core import LOGGER
from operonx.core.configs import OpType
from operonx.core.ops import BaseOp
from operonx.core.ops.base import shorthand, split_shorthand_kwargs
from operonx.core.utils.common import Param
from operonx.providers.ops._utils import resolve_hub

__all__ = ["VectorSearchOp", "VectorStoreOpBase"]


class VectorStoreOpBase(BaseOp):
    """What every op over a ``vector_store:`` resource shares.

    Resolving the backend lazily from ResourceHub, adopting its ``bound``
    (a local FAISS index is CPU-bound, a networked store I/O-bound, and
    the op cannot know which until the resource resolves), and reporting
    store, backend and metric to traces. A subclass declares its ports in
    :meth:`_ports` and its work in ``_process``.
    """

    __slots__ = ["resource", "backend", "_initialized"]

    def __init__(
        self,
        resource: Optional[str] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs: Any,
    ):
        """Initialize the op.

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

        input_schema, output_schema = self._ports()
        self.inputs = self._merge_params(input_schema, inputs)
        self.outputs = self._merge_params(output_schema, outputs)

        self.backend = None
        self._initialized = False
        self._set_core(self._process)

    @abstractmethod
    def _ports(self) -> Tuple[Dict[str, Param], Dict[str, Param]]:
        """``(input_schema, output_schema)``, built fresh per instance."""

    @property
    def _key(self) -> str:
        """The ResourceHub key ``resource`` names."""
        return self.resource if ":" in (self.resource or "") else f"vector_store:{self.resource}"

    def warmup(self) -> None:
        """Eagerly resolve the backend on engine startup."""
        self._ensure_initialized()

    def _ensure_initialized(self):
        """Lazy-resolve the backend from ResourceHub on first use, adopting
        its ``bound`` hint."""
        if self._initialized:
            return
        hub = resolve_hub()
        self.backend = hub.get(self._key)
        backend_bound = getattr(self.backend, "bound", None)
        if backend_bound:
            self.bound = backend_bound
        self._initialized = True

    @shorthand
    def of(cls, resource=None, **kwargs):
        """Create the op with flat kwargs.

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
        """Store name, plus backend and metric once the resource resolved."""
        meta: Dict[str, Any] = {"store": self.resource}
        if self.backend and hasattr(self.backend, "config"):
            cfg = self.backend.config
            meta["backend"] = getattr(cfg.api_type, "value", cfg.api_type)
            meta["metric"] = getattr(cfg.metric, "value", cfg.metric)
        return meta


class VectorSearchOp(VectorStoreOpBase):
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
        empty_index (bool): True when an unfiltered search found nothing,
            which a nearest-neighbour index does only when it holds no
            vectors. A WARNING is logged too: an index nobody populated
            otherwise answers every question with "no documents", and
            the pipeline downstream reads that as a real answer. Under a
            ``filter``, no hits is a legitimate answer and is not flagged.

    Example::

        hits = VectorSearchOp.of(
            resource="docs",
            query_vector=emb["embeddings"][0],
            top_k=20,
            filter={"tenant": "acme"},
        )
        docs = DocFetchOp.of(resource="main", ids=hits["ids"], collection="docs")
    """

    show_keys_default = ("ids", "scores")

    __slots__ = []

    type: OpType = "vector-search"

    def _ports(self):
        return (
            {
                "query_vector": Param(type=list, required=True),
                "top_k": Param(type=int, required=False, default=10),
                "filter": Param(type=(dict, str), required=False, default=None),
                "collection": Param(type=str, required=False, default=None),
            },
            {
                "ids": Param(type=list, required=True),
                "scores": Param(type=list, required=True),
                "metadata": Param(type=list, required=False),
                "empty_index": Param(type=bool, required=False),
            },
        )

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
        # Without a filter, a k-NN search returns hits whenever the index
        # holds any vector, so none means an empty index. Under a filter it
        # may just mean nothing matched, and that is an answer.
        empty_index = not ids and filter is None and top_k > 0
        if empty_index:
            LOGGER.warning(
                "VectorSearchOp %s: %s (collection %s) holds no vectors, so every "
                "search returns nothing. Populate it first, e.g. with VectorUpsertOp.",
                self.full_name,
                self._key,
                collection or "default",
            )
        return {"ids": ids, "scores": scores, "metadata": metadata, "empty_index": empty_index}
