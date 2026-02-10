"""Rerank Node for hush-providers.

This module provides RerankNode that uses ResourceHub to access reranker resources.
Follows hush-core design patterns with Param-based schema.
"""

from typing import Any, Dict, List, Optional

from hush.core.configs import NodeType
from hush.core.exceptions import RerankError
from hush.core.nodes import BaseNode
from hush.core.registry import ResourceHub, get_hub
from hush.core.utils.common import Param


class RerankNode(BaseNode):
    """Reranking node for scoring and re-ordering documents in workflows.

    Uses ResourceHub to access reranker resources by resource_key.

    Example:
        ```python
        from hush.core import GraphNode, START, END, PARENT
        from hush.providers import RerankNode

        with GraphNode(name="rerank") as workflow:
            rerank = RerankNode(
                name="rerank",
                resource_key="bge-m3",
                inputs={"query": PARENT["query"], "documents": PARENT["documents"]},
                outputs={"*": PARENT}
            )
            START >> rerank >> END

        workflow.build()
        ```
    """

    __slots__ = ["resource_key", "backend"]

    type: NodeType = "rerank"

    def __init__(
        self,
        resource_key: Optional[str] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs,
    ):
        """Initialize RerankNode.

        Args:
            resource_key: Resource key for reranker in ResourceHub (e.g., "bge-m3")
            inputs: Input variable mappings
            outputs: Output variable mappings
            **kwargs: Additional keyword arguments for BaseNode
        """
        super().__init__(**kwargs)

        self.resource_key = resource_key

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

        # Get reranker from ResourceHub
        try:
            hub = ResourceHub.instance()
        except RuntimeError:
            hub = get_hub()

        self.backend = hub.reranker(self.resource_key)
        self.core = self._process

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
                resource_key=self.resource_key or "unknown",
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
                resource_key=self.resource_key or "unknown",
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

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return rerank-specific metadata dictionary."""
        return {"model": self.resource_key}


def rerank_(resource_key=None, **kwargs) -> RerankNode:
    """Shorthand to create a RerankNode with flat kwargs.

    Example:
        rerank = rerank_("bge-m3", query=PARENT["q"], documents=PARENT["docs"], outputs={"*": PARENT})
    """
    from hush.core.nodes import split_shorthand_kwargs

    _skip_auto_name = True  # noqa: F841
    input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
    return RerankNode(resource_key=resource_key, inputs=input_mappings or None, **init_kwargs)
