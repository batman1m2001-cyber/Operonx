"""ONNX inference resource plugin for ResourceHub.

Auto-registers ONNX inference config class and factory handler with hush-icore.
"""

from hush.core.registry import REGISTRY
from hush.providers.onnx.config import OnnxInferenceConfig
from hush.providers.onnx.factory import create_onnx

_registered = False


def register():
    """Register ONNX inference config class and factory handler."""
    global _registered
    if _registered:
        return

    REGISTRY.register(OnnxInferenceConfig, create_onnx)
    _registered = True


def is_registered() -> bool:
    """Check if plugin has been registered."""
    return _registered


# Auto-register on import
register()
