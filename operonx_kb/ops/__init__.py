"""operonx-kb ops: the logic. Graphs in :mod:`operonx_kb.graphs` only wire them."""

from operonx_kb.ops.embed import embed_chunks, embedder_fingerprint
from operonx_kb.ops.ingest import (
    build_tree,
    chunk_version,
    commit_version,
    forget_index_writes,
    parse_document,
    plan_ingest,
    removed_vector_ids,
    report,
    skipped,
    stage_index_writes,
)

__all__ = [
    "embed_chunks",
    "build_tree",
    "chunk_version",
    "forget_index_writes",
    "removed_vector_ids",
    "commit_version",
    "embedder_fingerprint",
    "parse_document",
    "plan_ingest",
    "report",
    "skipped",
    "stage_index_writes",
]
