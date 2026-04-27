"""ONNX inference provider — run arbitrary ONNX models (classifiers, transformers)."""

from operonx.providers.onnx.backend import OnnxInferenceBackend
from operonx.providers.onnx.config import OnnxInferenceConfig

__all__ = ["OnnxInferenceBackend", "OnnxInferenceConfig"]
