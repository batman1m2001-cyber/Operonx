"""Enrichment: what a model adds to a version before it is indexed (PLAN §9).

Pure functions: what each stage asks a model, how a request is keyed, and how
answers become embed texts and tree nodes. The model calls themselves are
``LLMOp``\\ s in :mod:`operonx_kb.graphs.enrich`, behind the catalog's cache.

- :mod:`.base`: a request is ``{"key", "messages"}``, keyed by the hash of its messages.
- :mod:`.contextual`: section windows and the context prompt (PLAN E2, E3).
- :mod:`.tree`: tree nodes from headings or a synthesized table of contents,
  summaries, and the navigator of tree search (PLAN E5-E7).
"""

from operonx_kb.enrich.base import enricher_fingerprint, make_request, request_key, truncate

__all__ = ["enricher_fingerprint", "make_request", "request_key", "truncate"]
