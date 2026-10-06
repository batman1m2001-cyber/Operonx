"""Chunkers: the structural default and the recursive baseline (track5 §7.4)."""

from operonx_kb.chunking.base import ChunkDraft, Chunker, embed_text, heading_paths, materialize
from operonx_kb.chunking.recursive import RecursiveChunker
from operonx_kb.chunking.structural import StructuralChunker
from operonx_kb.model.collection import ChunkerSpec

__all__ = [
    "ChunkDraft",
    "Chunker",
    "RecursiveChunker",
    "StructuralChunker",
    "chunker_from_spec",
    "embed_text",
    "heading_paths",
    "materialize",
]


def chunker_from_spec(spec: ChunkerSpec) -> Chunker:
    """The chunker a collection spec names."""
    if spec.kind == "recursive":
        return RecursiveChunker(max_tokens=spec.max_tokens, heading_context=spec.heading_context)
    return StructuralChunker(
        max_tokens=spec.max_tokens, min_tokens=spec.min_tokens, heading_context=spec.heading_context
    )
