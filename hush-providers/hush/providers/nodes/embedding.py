"""Embedding Node for hush-providers.

This module provides EmbeddingNode that uses ResourceHub to access embedding resources.
Follows hush-core design patterns with Param-based schema.
"""

from typing import Any, Dict, List, Optional, Union

from hush.core.configs import NodeType
from hush.core.exceptions import EmbeddingError
from hush.core.nodes import BaseNode
from hush.core.registry import ResourceHub, get_hub
from hush.core.utils.common import Param


class EmbeddingNode(BaseNode):
    """Embedding node for converting text to vector embeddings in workflows.

    Uses ResourceHub to access embedding resources by resource_key.

    Example:
        ```python
        from hush.core import GraphNode, START, END, PARENT
        from hush.providers import EmbeddingNode

        with GraphNode(name="embed") as workflow:
            embed = EmbeddingNode(
                name="embed",
                resource_key="bge-m3",
                inputs={"texts": PARENT["texts"]},
                outputs={"*": PARENT}
            )
            START >> embed >> END

        workflow.build()
        ```
    """

    __slots__ = ["resource_key", "backend"]

    type: NodeType = "embedding"

    def __init__(
        self,
        resource_key: Optional[str] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs,
    ):
        """Initialize EmbeddingNode.

        Args:
            resource_key: Resource key for embedding model in ResourceHub (e.g., "bge-m3")
            inputs: Input variable mappings
            outputs: Output variable mappings
            **kwargs: Additional keyword arguments for BaseNode
        """
        super().__init__(**kwargs)

        self.resource_key = resource_key

        # Define input/output schema
        input_schema = {
            "texts": Param(type=list, required=True),
        }

        output_schema = {
            "embeddings": Param(type=list, required=True),
        }

        # Merge with user-provided
        self.inputs = self._merge_params(input_schema, inputs)
        self.outputs = self._merge_params(output_schema, outputs)

        # Get embedder from ResourceHub
        try:
            hub = ResourceHub.instance()
        except RuntimeError:
            hub = get_hub()

        self.backend = hub.embedding(self.resource_key)
        self.core = self._process

    async def _process(self, texts: Union[str, List[str]]) -> Dict[str, Any]:
        """Process texts and return embeddings."""
        text_list = [texts] if isinstance(texts, str) else texts
        try:
            result = await self.backend.run(texts)
            # backend.run() returns {"embeddings": [[...], [...], ...]}
            return result
        except EmbeddingError:
            raise  # Already wrapped
        except Exception as e:
            raise EmbeddingError(
                message="Embedding backend failed",
                resource_key=self.resource_key or "unknown",
                text_count=len(text_list),
                original_error=e,
            ) from e

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return embedding-specific metadata dictionary."""
        return {"model": self.resource_key}


def embedding_(resource_key=None, **kwargs) -> EmbeddingNode:
    """Shorthand to create an EmbeddingNode with flat kwargs.

    Example:
        embed = embedding_("bge-m3", texts=PARENT["texts"], outputs={"*": PARENT})
    """
    from hush.core.nodes import split_shorthand_kwargs

    _skip_auto_name = True  # noqa: F841
    input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
    return EmbeddingNode(resource_key=resource_key, inputs=input_mappings or None, **init_kwargs)
