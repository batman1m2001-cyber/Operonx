"""EmbeddingOp — converts text to vector embeddings via ResourceHub."""

from typing import Any, Dict, List, Optional, Union

from operonx.core.configs import OpType
from operonx.core.exceptions import EmbeddingError
from operonx.core.ops import BaseOp
from operonx.core.ops.base import shorthand, split_shorthand_kwargs
from operonx.core.utils.common import Param
from operonx.providers.ops._utils import resolve_hub


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

    __slots__ = ["resource", "backend", "_initialized"]

    type: OpType = "embedding"

    def __init__(
        self,
        resource: Optional[str] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs: Any,
    ):
        """Initialize EmbeddingOp.

        Args:
            resource: Resource key for embedding model in ResourceHub (e.g., "bge-m3")
            inputs: Input variable mappings
            outputs: Output variable mappings
            **kwargs: Additional keyword arguments for BaseOp
        """
        # Provider ops are I/O-bound by default (HTTP calls to embedding backends)
        kwargs.setdefault("bound", "io")
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

        # Embedding backend — lazy-initialized on first use to allow
        # graph construction before ResourceHub is set up
        self.backend = None
        self._initialized = False
        self._set_core(self._process)

    def warmup(self) -> None:
        """Eagerly initialize embedding backend on engine startup."""
        self._ensure_initialized()

    def _ensure_initialized(self):
        """Lazy-init embedding backend from ResourceHub on first use."""
        if self._initialized:
            return
        hub = resolve_hub()
        self.backend = hub.get(f"embedding:{self.resource}")
        self._initialized = True

    async def _process(self, texts: Union[str, List[str]]) -> Dict[str, Any]:
        """Process texts and return embeddings."""
        self._ensure_initialized()
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

    def serialize(self) -> dict:
        """Serialize EmbeddingOp for Rust backend, including backend config."""
        self._ensure_initialized()
        base = super().serialize()
        base["resource"] = self.resource
        if self.backend and hasattr(self.backend, "config"):
            base["resource_config"] = self.backend.config.model_dump(mode="json")
        return base

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return embedding-specific metadata dictionary."""
        return {"model": self.resource}
