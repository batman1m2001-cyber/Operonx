"""Restore score order after a key-based fetch.

The footgun this exists for (plan §5.3): ``VectorSearchOp`` returns ids in
score order, but ``SELECT … WHERE id = ANY(…)`` — and Mongo's ``$in``, and
Redis pipelines — return rows in *arbitrary* order. Zipping them naively
pairs every document with the wrong score, silently, with no error.

``DocFetchOp`` applies this for you. It is exported for anyone writing
their own fetch op against a store operonx doesn't ship a backend for.
"""

from typing import Any, Dict, List, Sequence, Tuple

__all__ = ["reorder_by_ids", "partition_by_ids"]


def reorder_by_ids(
    rows: Sequence[Dict[str, Any]],
    ids: Sequence[Any],
    key: str = "id",
) -> List[Dict[str, Any]]:
    """Return ``rows`` ordered to match ``ids``, dropping ids with no row.

    Args:
        rows: Fetched records, in any order.
        ids: Desired order — typically ``VectorSearchOp``'s ``ids`` output.
        key: Field on each row holding its primary key.

    Returns:
        Rows in ``ids`` order. Ids with no matching row are skipped; use
        :func:`partition_by_ids` when you need to know which.

    Note:
        Ids are matched by value, so an ``int`` id from the vector index
        will not match a ``str`` key from the document store. Keep the two
        stores' key types aligned.
    """
    ordered, _ = partition_by_ids(rows, ids, key)
    return ordered


def partition_by_ids(
    rows: Sequence[Dict[str, Any]],
    ids: Sequence[Any],
    key: str = "id",
) -> Tuple[List[Dict[str, Any]], List[Any]]:
    """Split a fetch result into ordered rows and missing ids.

    Missing ids are a real condition, not noise: they mean the derived
    index has drifted from the store of record (deleted document, failed
    sync). Surfacing them beats silently returning a shorter list.

    Args:
        rows: Fetched records, in any order.
        ids: Desired order.
        key: Field on each row holding its primary key.

    Returns:
        ``(ordered_rows, missing_ids)``. Duplicate ids in ``ids`` each
        yield the same row; duplicate keys in ``rows`` keep the first.
    """
    by_id: Dict[Any, Dict[str, Any]] = {}
    for row in rows:
        if key in row:
            by_id.setdefault(row[key], row)

    ordered: List[Dict[str, Any]] = []
    missing: List[Any] = []
    for id_ in ids:
        row = by_id.get(id_)
        if row is None:
            missing.append(id_)
        else:
            ordered.append(row)
    return ordered, missing
