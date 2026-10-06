"""Retrieval and answer metrics over quote-anchored labels (track5 §15.3, PLAN R8). Pure.

A label is a quote in a document. At eval time it is resolved to every span of
the document's active version where the quote occurs (:mod:`.labels`), so a
label survives a change of chunker or parser. A hit **covers** a label when it
belongs to that version and its spans hold at least half of one occurrence's
characters: a chunk that holds the sentence, not one that grazes its edge.

Each label counts once: Recall@k is the share of labels covered by the top k,
MRR the reciprocal rank of the first hit covering any label, nDCG@k gains 1
for a hit that covers a label no better-ranked hit covered.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from operonx_kb.model.collection import AnalyzerSpec
from operonx_kb.model.document import Span
from operonx_kb.retrieval.citations import MARKER, answer_sentences
from operonx_kb.text.analyze import Analyzer

__all__ = [
    "Occurrence",
    "covers",
    "recall_at",
    "reciprocal_rank",
    "ndcg_at",
    "citation_precision",
    "faithfulness_proxy",
    "grounded_recall",
]

#: Where a label's quote occurs: ``(version_id, span)``.
Occurrence = Tuple[str, Span]
_WORDS = Analyzer(AnalyzerSpec())


def _overlap(spans: Sequence[Sequence[int]], span: Span) -> int:
    return sum(max(0, min(e, span[1]) - max(s, span[0])) for s, e in spans)


def covers(hit: Mapping[str, Any], label: Sequence[Occurrence], share: float = 0.5) -> bool:
    """Whether a hydrated hit holds at least ``share`` of one occurrence of the label."""
    for version, span in label:
        if hit.get("version_id") == version and span[1] > span[0]:
            if _overlap(hit.get("spans") or [], span) >= share * (span[1] - span[0]):
                return True
    return False


def _covered(hits: Sequence[Mapping[str, Any]], labels: Sequence[Sequence[Occurrence]]):
    """For each hit in order, the labels it covers that no earlier hit covered."""
    seen: set = set()
    out = []
    for hit in hits:
        new = {i for i, label in enumerate(labels) if i not in seen and covers(hit, label)}
        seen |= new
        out.append(new)
    return out


def recall_at(
    hits: Sequence[Mapping[str, Any]], labels: Sequence[Sequence[Occurrence]], k: int
) -> float:
    if not labels:
        raise ValueError("a retrieval case needs at least one relevant label")
    found = set().union(*_covered(hits[:k], labels)) if hits[:k] else set()
    return len(found) / len(labels)


def reciprocal_rank(
    hits: Sequence[Mapping[str, Any]], labels: Sequence[Sequence[Occurrence]]
) -> float:
    for rank, new in enumerate(_covered(hits, labels), start=1):
        if new:
            return 1.0 / rank
    return 0.0


def ndcg_at(
    hits: Sequence[Mapping[str, Any]], labels: Sequence[Sequence[Occurrence]], k: int
) -> float:
    gains = [len(new) > 0 for new in _covered(hits[:k], labels)]
    dcg = sum(1.0 / math.log2(rank + 1) for rank, g in enumerate(gains, start=1) if g)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(labels), k) + 1))
    return dcg / ideal if ideal else 0.0


def citation_precision(answer: Mapping[str, Any]) -> Optional[float]:
    """Verified citations over all the model gave; ``None`` when it gave none."""
    verified, dropped = len(answer.get("citations") or []), len(answer.get("dropped") or [])
    return verified / (verified + dropped) if verified + dropped else None


def faithfulness_proxy(answer: Mapping[str, Any]) -> float:
    """The mean, over answer sentences, of the share of a sentence's words found in the
    verified quotes it cites (0 for a sentence that cites nothing verified).

    A lexical stand-in for entailment: it rewards answers that say what the cited
    text says, and gives nothing to claims without a verified citation.
    """
    text = answer.get("text") or ""
    sentences = answer_sentences(text)
    if not sentences:
        return 0.0
    quotes: Dict[int, set] = {}
    for c in answer.get("citations") or []:
        quotes.setdefault(int(c["source"]), set()).update(_WORDS.tokens(c["quote"]))
    scores = []
    for s, e in sentences:
        sentence = text[s:e]
        cited = {
            int(n) for m in MARKER.finditer(sentence) for n in re.split(r"\s*,\s*", m.group(1))
        }
        words = set(_WORDS.tokens(MARKER.sub(" ", sentence)))
        support = set().union(*(quotes.get(n, set()) for n in cited)) if cited else set()
        scores.append(len(words & support) / len(words) if words else 0.0)
    return sum(scores) / len(scores)


def grounded_recall(answer: Mapping[str, Any], labels: Sequence[Sequence[Occurrence]]) -> float:
    """The share of labels a verified citation of the answer covers."""
    if not labels:
        raise ValueError("an answer case needs at least one relevant label")
    cites = [
        {"version_id": c["version_id"], "spans": [c["span"]]} for c in answer.get("citations") or []
    ]
    return sum(any(covers(c, label) for c in cites) for label in labels) / len(labels)
