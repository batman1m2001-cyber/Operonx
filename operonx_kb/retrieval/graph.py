"""The concept graph of a collection and personalized PageRank over it (PLAN G3, G4).

The graph is bipartite: chunks on one side, concepts on the other, an edge
where a chunk names a concept (weighted, :mod:`operonx_kb.enrich.concepts`).
It is built from the catalog's mentions of the active versions and kept in
memory per collection **generation** — the set of active versions — so a commit,
a delete or a purge is seen by the next search (track5 §9.6: "a cached CSR
adjacency per collection generation"; numpy edge arrays here, no scipy).

A walk restarts at the seed chunks with probability ``alpha`` and otherwise
moves chunk → concept → chunk along edges in proportion to their weights
(HippoRAG 2's PPR on a passage-entity graph). A concept in too many chunks
links nothing in particular and is left out (``max_df``).

A filtered search walks only the chunks of the documents the filter allows:
mass that would enter any other chunk restarts instead, so a document the
filter hides neither shows up nor carries the walk to anything else.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Collection, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

__all__ = ["ConceptGraph", "graph_for", "generation"]

#: Collection graphs kept in memory (each holds its edge arrays).
CACHED = 16


class ConceptGraph:
    """The chunk-concept graph of one collection generation.

    Args:
        mentions: ``(chunk_id, document_id, concept, weight)`` rows.
        max_df_share: A concept in more than this share of the chunks …
        max_df_min: … and in more than this many is left out.
    """

    def __init__(
        self,
        mentions: Sequence[Tuple[str, str, str, float]],
        *,
        max_df_share: float,
        max_df_min: int,
    ):
        chunks: Dict[str, int] = {}
        documents: List[str] = []
        df: Dict[str, int] = {}
        for chunk_id, document_id, concept, _ in mentions:
            if chunk_id not in chunks:
                chunks[chunk_id] = len(chunks)
                documents.append(document_id)
            df[concept] = df.get(concept, 0) + 1
        limit = max(max_df_min, int(max_df_share * len(chunks)))
        concepts: Dict[str, int] = {}
        rows, cols, weights = [], [], []
        for chunk_id, _, concept, weight in mentions:
            if df[concept] > limit or weight <= 0:
                continue
            rows.append(chunks[chunk_id])
            cols.append(concepts.setdefault(concept, len(concepts)))
            weights.append(weight)
        self.chunk_ids: List[str] = list(chunks)
        self.chunk_index = chunks
        self.documents = np.asarray(documents, dtype=object)
        self.concepts = len(concepts)
        self.dropped = sum(1 for n in df.values() if n > limit)
        self._rows = np.asarray(rows, dtype=np.int64)
        self._cols = np.asarray(cols, dtype=np.int64)
        self._w = np.asarray(weights, dtype=np.float64)

    @property
    def edges(self) -> int:
        return int(len(self._rows))

    def rank(
        self,
        seeds: Mapping[str, float],
        *,
        alpha: float,
        iterations: int,
        documents: Optional[Collection[str]] = None,
    ) -> List[Tuple[str, float]]:
        """Chunks by their PPR mass from ``seeds`` (``chunk_id → restart weight``),
        best first; chunks the walk never reaches are absent. ``documents``, when
        given, are the only documents whose chunks the walk may enter. A seed the
        graph does not hold (a chunk that names nothing) keeps its restart mass."""
        n = len(self.chunk_ids)
        allowed = (
            np.ones(n, dtype=bool)
            if documents is None
            else np.isin(self.documents, list(documents)) if n else np.zeros(0, dtype=bool)
        )  # fmt: skip
        restart = np.zeros(n)
        outside: Dict[str, float] = {}
        for chunk_id, weight in seeds.items():
            i = self.chunk_index.get(chunk_id)
            if i is None:
                outside[chunk_id] = weight
            elif allowed[i]:
                restart[i] += weight
        total = restart.sum() + sum(outside.values())
        if total <= 0:
            return []
        outside_ranked = [(c, w / total) for c, w in outside.items()]
        restart /= total
        inside = restart.sum()
        if inside <= 0:
            return sorted(outside_ranked, key=lambda x: -x[1])
        # the walk's edges: those between allowed chunks and concepts
        keep = allowed[self._rows] if len(self._rows) else np.zeros(0, dtype=bool)
        rows, cols, w = self._rows[keep], self._cols[keep], self._w[keep]
        to_concept = w / np.maximum(np.bincount(rows, weights=w, minlength=n)[rows], 1e-12)
        to_chunk = w / np.maximum(
            np.bincount(cols, weights=w, minlength=self.concepts)[cols], 1e-12
        )
        chunk_p, concept_p = restart.copy(), np.zeros(self.concepts)
        for _ in range(iterations):
            moved_c = np.bincount(rows, weights=to_chunk * concept_p[cols], minlength=n)
            moved_t = np.bincount(cols, weights=to_concept * chunk_p[rows], minlength=self.concepts)
            stuck = chunk_p.sum() + concept_p.sum() - moved_c.sum() - moved_t.sum()
            chunk_p = alpha * restart + (1 - alpha) * (moved_c + stuck * restart / inside)
            concept_p = (1 - alpha) * moved_t
        out = [(self.chunk_ids[i], float(chunk_p[i])) for i in np.flatnonzero(chunk_p > 0)]
        return sorted(out + outside_ranked, key=lambda x: -x[1])


_lock = threading.Lock()
_graphs: "OrderedDict[tuple, ConceptGraph]" = OrderedDict()


def generation(version_ids: Sequence[str]) -> str:
    """A collection's generation: the hash of its active version ids."""
    return hashlib.sha256("\n".join(sorted(version_ids)).encode()).hexdigest()


def graph_for(
    catalog, catalog_key: str, collection: str, *, max_df_share: float, max_df_min: int
) -> ConceptGraph:
    """The collection's graph for its current generation: built from the catalog's
    mentions on first use and after any commit, delete or purge; cached otherwise."""
    key = (catalog_key, collection, generation(catalog.active_version_ids(collection)),
           max_df_share, max_df_min)  # fmt: skip
    with _lock:
        found = _graphs.get(key)
        if found is not None:
            _graphs.move_to_end(key)
            return found
    built = ConceptGraph(
        catalog.graph_mentions(collection), max_df_share=max_df_share, max_df_min=max_df_min
    )
    with _lock:
        _graphs[key] = built
        while len(_graphs) > CACHED:
            _graphs.popitem(last=False)
    return built
