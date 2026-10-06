"""Door ops: a request item in, a result item out (the logic of the ``*_flow`` graphs)."""

from __future__ import annotations

from typing import Optional

from operonx import op

__all__ = ["search_request", "search_result", "answer_result"]


@op(show_keys="query")
def search_request(item: dict, k: int = 10) -> dict:
    """``{"query", "collection", "filter"?, "k"?}`` as the search's inputs.

    Raises:
        ValueError: ``query`` or ``collection`` is missing.
    """
    missing = [name for name in ("query", "collection") if not item.get(name)]
    if missing:
        raise ValueError(f"a search request needs {missing}; got keys {sorted(item)}")
    return {
        "query": str(item["query"]),
        "collection": str(item["collection"]),
        "filter": item.get("filter"),
        "k": int(item.get("k") or k),
    }


@op(exclude={"trace": ["hits"]}, show_keys="result")
def search_result(hits: list, stats: Optional[dict] = None) -> dict:
    """What a search flow sends back."""
    return {"result": {"hits": hits, "stats": stats or {}}}


@op(show_keys="result")
def answer_result(answer: dict) -> dict:
    """What an answer flow sends back."""
    return {"result": answer}
