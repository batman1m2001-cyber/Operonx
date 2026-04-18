"""ONNX inference provider — run arbitrary ONNX models (classifiers, transformers)."""

from operon.providers.onnx.backend import OnnxInferenceBackend
from operon.providers.onnx.config import OnnxInferenceConfig

__all__ = ["OnnxInferenceBackend", "OnnxInferenceConfig"]
