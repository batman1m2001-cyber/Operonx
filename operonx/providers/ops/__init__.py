"""Workflow nodes for AI providers.

All provider ops resolve their backend via ResourceHub and carry no
heavy module-level deps, so they are imported eagerly.

Note (1.2.0): ``OnnxOp`` and ``TritonOp`` were removed. Both named their
*transport* rather than a semantic, so every backend needed its own op.
Replacements:

- Triton-hosted models — write a bare ``@op`` around
  :class:`operonx.providers.triton.TritonClient`, which supplies the
  pooled gRPC client, dtype translation and output decoding.
- ONNX models — write a bare ``@op`` around
  :func:`operonx.providers._utils.onnx.load_onnx_session`. ONNX also
  remains a *backend* for ``EmbeddingOp`` and ``RerankOp``.

See MIGRATION.md for recipes.

Note (1.0.0): the ``ask()`` helper was removed. Its behaviour lives in
``LLMOp(fields=..., parser=..., validators=..., max_retries=...)`` — LLMOp
now does inline parsing + error-guided semantic retry in a single node.
"""

from operonx.providers.ops.doc_fetch import DocFetchOp
from operonx.providers.ops.embedding import EmbeddingOp
from operonx.providers.ops.llm import LLMOp
from operonx.providers.ops.rerank import RerankOp
from operonx.providers.ops.vector_search import VectorSearchOp

__all__ = [
    "LLMOp",
    "EmbeddingOp",
    "RerankOp",
    "VectorSearchOp",
    "DocFetchOp",
]
