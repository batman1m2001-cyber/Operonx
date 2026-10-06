"""Test helpers: fakes (counting embedder), golden snapshots, trace recording."""

from operonx_kb.testing.fakes import (
    HashEmbedder,
    HashEmbeddingConfig,
    OverlapReranker,
    ScriptedLLM,
    register_fakes,
)
from operonx_kb.testing.golden import compare_or_update, tree_snapshot
from operonx_kb.testing.trace import RecordingConsumer

__all__ = [
    "HashEmbedder",
    "HashEmbeddingConfig",
    "OverlapReranker",
    "ScriptedLLM",
    "RecordingConsumer",
    "compare_or_update",
    "register_fakes",
    "tree_snapshot",
]
