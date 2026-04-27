"""Factory for ONNX inference backends.

Backend is lazy-imported so the registry plugin (which imports this
factory at module load) doesn't pull numpy / onnxruntime / tokenizers
into every ``import operonx.providers`` chain.
"""

from operonx.providers.onnx.config import OnnxInferenceConfig


def create_onnx(config: OnnxInferenceConfig):
    """Create an ONNX inference backend from config."""
    try:
        from operonx.providers.onnx.backend import OnnxInferenceBackend
    except ImportError as exc:
        raise ImportError(
            "OnnxInferenceBackend requires additional packages.\n"
            "  Install with: pip install operonx[onnx]\n"
            f"  Original error: {exc}"
        ) from exc
    return OnnxInferenceBackend(config)
