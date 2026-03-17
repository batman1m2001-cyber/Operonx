"""Factory for ONNX inference backends."""

from hush.providers.onnx.backend import OnnxInferenceBackend
from hush.providers.onnx.config import OnnxInferenceConfig


def create_onnx(config: OnnxInferenceConfig) -> OnnxInferenceBackend:
    """Create an ONNX inference backend from config."""
    return OnnxInferenceBackend(config)
