"""Workflow nodes for AI providers.

Light ops (``LLMOp``, ``EmbeddingOp``, ``RerankOp``, ``OnnxOp``, ``ask``)
only resolve resources via the hub — no heavy module-level deps. They're
imported eagerly.

``TritonOp`` is lazy-loaded because ``triton.py`` imports numpy at
module level. Users who don't need Triton don't need numpy.
"""

from operonx.providers.ops.ask import ask
from operonx.providers.ops.embedding import EmbeddingOp
from operonx.providers.ops.llm import LLMOp
from operonx.providers.ops.onnx import OnnxOp
from operonx.providers.ops.rerank import RerankOp


def __getattr__(name: str):
    if name == "TritonOp":
        try:
            from operonx.providers.ops.triton import TritonOp as _TritonOp
        except ImportError as exc:
            raise ImportError(
                "TritonOp requires additional packages.\n"
                "  Install with: pip install operonx[providers]\n"
                f"  Original error: {exc}"
            ) from exc
        globals()["TritonOp"] = _TritonOp
        return _TritonOp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "LLMOp",
    "EmbeddingOp",
    "RerankOp",
    "OnnxOp",
    "TritonOp",
    "ask",
]
