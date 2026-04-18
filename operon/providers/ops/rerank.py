"""RerankOp — scores and re-orders documents via ResourceHub."""

from typing import Any, Dict, List, Optional

from operon.core.configs import OpType
from operon.core.exceptions import RerankError
from operon.core.ops import BaseOp
from operon.core.ops.base import shorthand, split_shorthand_kwargs
from operon.core.utils.common import Param
from operon.providers.ops._utils import resolve_hub


class RerankOp(BaseOp):
    """Op that scores and re-orders documents by relevance to a query.

    Wraps a reranker backend (e.g. BGE-M3, Pinecone, TEI) accessed via
    ResourceHub. Returns documents sorted by relevance score.

    Inputs:
        query (str): The query to rank against. Required.
        documents (list[str]): Documents to rerank. Required.
        top_k (int): Max results to return. Default: -1 (all).
        threshold (float): Min score cutoff. Default: 0.0.

    Outputs:
        reranks (list[dict]): Reranked results with ``index``, ``score``,
            and ``document`` fields.

    Example::

        rerank = RerankOp.of(
            resource="bge-m3",
            query=PARENT["query"],
            documents=PARENT["docs"],
            top_k=5,
        )
    """

    __slots__ = ["resource", "backend", "_initialized"]

    type: OpType = "rerank"

    def __init__(
        self,
        resource: Optional[str] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs: Any,
    ):
        """Initialize RerankOp.

        Args:
            resource: Resource key for reranker in ResourceHub (e.g., "bge-m3")
            inputs: Input variable mappings
            outputs: Output variable mappings
            **kwargs: Additional keyword arguments for BaseOp
        """
        # Provider ops are I/O-bound by default (HTTP calls to reranker backends)
        kwargs.setdefault("bound", "io")
        super().__init__(**kwargs)

        self.resource = resource

        # Define input/output schema
        input_schema = {
            "query": Param(type=str, required=True),
            "documents": Param(type=list, required=True),
            "top_k": Param(type=int, default=-1),
            "threshold": Param(type=float, default=0.0),
        }

        output_schema = {
            "reranks": Param(type=list, required=True),
        }

        # Merge with user-provided
        self.inputs = self._merge_params(input_schema, inputs)
        self.outputs = self._merge_params(output_schema, outputs)

        # Reranker backend — lazy-initialized on first use to allow
        # graph construction before ResourceHub is set up
        self.backend = None
        self._initialized = False
        self._set_core(self._process)

    def warmup(self) -> None:
        """Eagerly initialize reranker backend on engine startup."""
        self._ensure_initialized()

    def _ensure_initialized(self):
        """Lazy-init reranker backend from ResourceHub on first use."""
        if self._initialized:
            return
        hub = resolve_hub()
        self.backend = hub.get(f"reranking:{self.resource}")
        self._initialized = True

    async def _process(
        self, query: str, documents: List, top_k: int, threshold: float
    ) -> Dict[str, Any]:
        """Process reranking request.

        Args:
            query: Search query
            documents: List of documents (str or dict)
            top_k: Number of top results to return (-1 for all)
            threshold: Minimum score threshold

        Returns:
            Dictionary with reranked documents
        """
        self._ensure_initialized()
        is_dict = False

        if not documents:  # Check if the list is empty
            # Handle empty list case
            return {"reranks": []}

        elif isinstance(documents[0], str):
            # documents is a List[str]
            texts = documents
        elif isinstance(documents[0], dict):
            is_dict = True
            # documents is a List[Dict]
            # Extract text field from each dictionary
            texts = [doc.get("content", "") for doc in documents]
        else:
            # Handle unexpected type
            raise RerankError(
                message="Invalid document type",
                resource=self.resource or "unknown",
                query=query,
                document_count=len(documents),
                original_error=TypeError(
                    f"Expected List[str] or List[Dict], got list of {type(documents[0]).__name__}"
                ),
            )

        try:
            results = await self.backend.run(
                query=query,
                texts=texts,
                top_k=top_k if top_k > 0 else len(texts),
                threshold=threshold,
            )
        except RerankError:
            raise  # Already wrapped
        except Exception as e:
            raise RerankError(
                message="Rerank backend failed",
                resource=self.resource or "unknown",
                query=query,
                document_count=len(documents),
                original_error=e,
            ) from e

        reranked_docs = []

        for r in results:
            if is_dict:
                reranked_docs.append({**documents[r["index"]], "score": r["score"]})
            else:
                reranked_docs.append({"content": documents[r["index"]], "score": r["score"]})

        return {"reranks": reranked_docs}

    @shorthand
    def of(cls, resource=None, **kwargs) -> "RerankOp":
        """Create a RerankOp with flat kwargs.

        Example::

            rerank = RerankOp.of(resource="bge-m3", query=PARENT["q"], documents=PARENT["docs"])
        """
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        return cls(resource=resource, inputs=input_mappings or None, **init_kwargs)

    def serialize(self) -> dict:
        """Serialize RerankOp for Rust backend, including backend config."""
        self._ensure_initialized()
        base = super().serialize()
        base["resource"] = self.resource
        if self.backend and hasattr(self.backend, "config"):
            base["resource_config"] = self.backend.config.model_dump(mode="json")
        return base

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return rerank-specific metadata dictionary."""
        return {"model": self.resource}
