"""ONNX inference config."""

from enum import Enum
from typing import ClassVar, Optional

from pydantic import BaseModel


class OnnxInputType(str, Enum):
    """Input type for ONNX model."""

    MLP = "mlp"  # (batch, dim) → (batch,) logits
    ATTENTION = "attention"  # (1, T, dim) + role_ids + mask → (1,) logit


class OnnxInferenceConfig(BaseModel):
    """Config for an ONNX inference model."""

    _category: ClassVar[str] = "onnx"

    model_path: str
    input_type: OnnxInputType = OnnxInputType.MLP
    pool_size: int = 1
    intra_threads: int = 1
    device: Optional[str] = None  # "cpu" or "cuda"
