"""Factory for ONNX inference backends."""

from operonx.providers.onnx.backend import OnnxInferenceBackend
from operonx.providers.onnx.config import OnnxInferenceConfig


def create_onnx(config: OnnxInferenceConfig) -> OnnxInferenceBackend:
    """Create an ONNX inference backend from config."""
    return OnnxInferenceBackend(config)
