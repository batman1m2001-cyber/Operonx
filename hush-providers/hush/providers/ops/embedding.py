"""EmbeddingOp — converts text to vector embeddings via ResourceHub."""

from typing import Any, Dict, List, Optional, Union

from hush.core.configs import OpType
from hush.core.exceptions import EmbeddingError
from hush.core.ops import BaseOp
from hush.core.ops.base import shorthand, split_shorthand_kwargs
from hush.core.registry import ResourceHub, get_hub
from hush.core.utils.common import Param


class EmbeddingOp(BaseOp):
    """Op that converts texts to vector embeddings via ResourceHub.

    Wraps an embedding backend (e.g. BGE-M3, OpenAI, TEI) and returns
    a list of embedding vectors matching the input order.

    Inputs:
        texts (list[str]): Texts to embed. Required.

    Outputs:
        embeddings (list[list[float]]): Embedding vectors.

    Example::

        embed = EmbeddingOp.of(resource="bge-m3", texts=PARENT["texts"])
    """

    __slots__ = ["resource", "backend"]

    type: OpType = "embedding"

    def __init__(
        self,
        resource: Optional[str] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs,
    ):
        """Initialize EmbeddingOp.

        Args:
            resource: Resource key for embedding model in ResourceHub (e.g., "bge-m3")
            inputs: Input variable mappings
            outputs: Output variable mappings
            **kwargs: Additional keyword arguments for BaseOp
        """
        super().__init__(**kwargs)

        self.resource = resource

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

        self.backend = hub.embedding(self.resource)
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
                resource=self.resource or "unknown",
                text_count=len(text_list),
                original_error=e,
            ) from e

    @shorthand
    def of(cls, resource=None, **kwargs) -> "EmbeddingOp":
        """Create an EmbeddingOp with flat kwargs.

        Example::

            embed = EmbeddingOp.of(resource="bge-m3", texts=PARENT["texts"], outputs={"*": PARENT})
        """
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        return cls(resource=resource, inputs=input_mappings or None, **init_kwargs)

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return embedding-specific metadata dictionary."""
        return {"model": self.resource}
