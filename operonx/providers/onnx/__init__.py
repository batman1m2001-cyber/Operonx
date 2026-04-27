"""ONNX inference provider — run arbitrary ONNX models (classifiers, transformers).

``OnnxInferenceBackend`` is lazy-loaded because its module imports numpy
at the top level. Users who only installed ``operonx[anthropic]`` can
still import ``operonx.providers.onnx`` (e.g. via the registry plugin
chain) — they only hit the missing-numpy error if they actually access
the backend class.
"""

from operonx.providers.onnx.config import OnnxInferenceConfig

_LAZY = {"OnnxInferenceBackend": "operonx.providers.onnx.backend"}


def __getattr__(name: str):
    if name in _LAZY:
        try:
            import importlib

            module = importlib.import_module(_LAZY[name])
        except ImportError as exc:
            raise ImportError(
                f"{name} requires additional packages.\n"
                "  Install with: pip install operonx[onnx]\n"
                f"  Original error: {exc}"
            ) from exc
        cls = getattr(module, name)
        globals()[name] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["OnnxInferenceBackend", "OnnxInferenceConfig"]
