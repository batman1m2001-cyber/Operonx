"""OnnxOp — run arbitrary ONNX models (classifiers, transformers) via ResourceHub."""

from typing import Any, Dict, List, Optional

from operon.core.configs import OpType
from operon.core.ops import BaseOp
from operon.core.ops.base import shorthand, split_shorthand_kwargs
from operon.core.utils.common import Param
from operon.providers.ops._utils import resolve_hub


class OnnxOp(BaseOp):
    """Op that runs ONNX inference models via ResourceHub.

    Supports two input types:
    - MLP: per-embedding classification → {"probabilities": [...]}
    - Attention: sequence classification → {"probability": float}

    Inputs:
        embeddings (list[list[float]]): Embedding vectors. Required.
        role_ids (list[int]): Role IDs per turn. Required for attention type.
        mask (list[bool]): Attention mask. Required for attention type.

    Outputs:
        probabilities (list[float]): Per-embedding sigmoid probs (MLP type).
        probability (float): Sequence-level sigmoid prob (attention type).

    Example::

        pred = OnnxOp.of(resource="sentiment-agent", embeddings=emb["embeddings"])
    """

    __slots__ = ["resource", "backend", "_initialized"]

    type: OpType = "onnx"

    def __init__(
        self,
        resource: Optional[str] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs: Any,
    ):
        # ONNX inference is CPU-bound
        kwargs.setdefault("bound", "cpu")
        super().__init__(**kwargs)

        self.resource = resource

        input_schema = {
            "embeddings": Param(type=list, required=True),
            "role_ids": Param(type=list, required=False),
            "mask": Param(type=list, required=False),
        }

        output_schema = {
            "probabilities": Param(type=list, required=False),
            "probability": Param(type=float, required=False),
        }

        self.inputs = self._merge_params(input_schema, inputs)
        self.outputs = self._merge_params(output_schema, outputs)

        # ONNX backend — lazy-initialized on first use to allow
        # graph construction before ResourceHub is set up
        self.backend = None
        self._initialized = False
        self._set_core(self._process)

    def warmup(self) -> None:
        """Eagerly initialize ONNX backend on engine startup."""
        self._ensure_initialized()

    def _ensure_initialized(self):
        """Lazy-init ONNX backend from ResourceHub on first use."""
        if self._initialized:
            return
        hub = resolve_hub()
        self.backend = hub.get(f"onnx:{self.resource}")
        self._initialized = True

    async def _process(
        self,
        embeddings: List[List[float]],
        role_ids: Optional[List[int]] = None,
        mask: Optional[List[bool]] = None,
    ) -> Dict[str, Any]:
        """Run ONNX inference on embeddings."""
        self._ensure_initialized()
        return await self.backend.run(embeddings, role_ids=role_ids, mask=mask)

    @shorthand
    def of(cls, resource=None, **kwargs) -> "OnnxOp":
        """Create an OnnxOp with flat kwargs.

        Example::

            pred = OnnxOp.of(resource="sentiment-agent", embeddings=emb["embeddings"])
        """
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        return cls(resource=resource, inputs=input_mappings or None, **init_kwargs)

    def serialize(self) -> dict:
        """Serialize OnnxOp for Rust backend."""
        self._ensure_initialized()
        base = super().serialize()
        base["resource"] = self.resource
        if self.backend and hasattr(self.backend, "config"):
            base["resource_config"] = self.backend.config.model_dump(mode="json")
        return base

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        return {"model": self.resource}
