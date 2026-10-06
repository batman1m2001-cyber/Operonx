"""One eval case from a reviewed query (track5 §15.3 "production promotion").

A case is a row of the KB's dataset format, an operonx ``Dataset`` line::

    {"id": "case_…", "input": {"query": …, "collection": …, "k"?: …},
     "expected": {"relevant": [{"doc_key": …, "quote": …, "page"?: …}], "answer"?: …},
     "tags": ["studio", …]}

Its labels are quotes, never chunk ids (:mod:`operonx_kb.eval.labels`), so the case
survives a re-parse or a re-chunk. A label is resolved before the case is made: a
case is never saved stale.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from operonx_kb.eval.labels import LabelError, LabelResolver
from operonx_kb.model.ids import make_id
from operonx_kb.text.normalize import normalize_inline

__all__ = ["eval_case", "case_id"]


def case_id(collection: str, query: str) -> str:
    """A case's id: the same question (up to whitespace and Unicode composition) in the
    same collection is the same case, so saving it twice adds it once."""
    return make_id("case", collection, normalize_inline(query))


def eval_case(
    kb: Any,
    collection: str,
    query: str,
    relevant: Sequence[Mapping[str, Any]],
    *,
    answer: Optional[str] = None,
    k: Optional[int] = None,
    tags: Sequence[str] = (),
) -> Dict[str, Any]:
    """The dataset row for ``query`` with ``relevant`` labels (``{"doc_key", "quote", "page"?}``).

    Args:
        kb: The :class:`~operonx_kb.kb.KnowledgeBase` the labels are resolved in.
        answer: The expected answer, when someone vouched for it.

    Raises:
        LabelError: The query is empty, there is no label, or a label does not resolve
            in the document's active version.
    """
    query = query.strip()
    if not query:
        raise LabelError("an eval case needs the question it asks")
    labels: List[Dict[str, Any]] = [dict(r) for r in relevant]
    if not labels:
        raise LabelError("an eval case needs at least one relevant quote")
    LabelResolver(kb.catalog_key, kb.blobs_key).resolve(collection, labels)
    expected: Dict[str, Any] = {"relevant": labels}
    if answer:
        expected["answer"] = answer
    row_input: Dict[str, Any] = {"query": query, "collection": collection}
    if k is not None:
        row_input["k"] = k
    return {
        "id": case_id(collection, query),
        "input": row_input,
        "expected": expected,
        "tags": list(dict.fromkeys(["studio", *tags])),
    }
