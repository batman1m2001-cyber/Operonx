"""Standard reducer library for shared cells declared via ``PARENT.declare()``.

A reducer is a plain function ``(old, new) -> merged`` applied at cell-write
time when the cell is a shared cell (index in ``schema._shared_indices``) AND
has a reducer registered. Without a reducer, shared cell writes use
last-write-wins semantics (unchanged from today).

Reducer application path:

    op returns {"messages": [...]}  → push-ref fires
                                    → target cell write hits _write_cell
                                    → reducer(old, new) → cell value

Reducers are applied on **every** shared write — direct set or push-ref hop.
See ``state.MemoryState._write_cell``.

Contents:
    - add_messages     — list-of-messages upsert with id dedup + Remove sentinels
    - dict_merge       — recursive dict merge (right wins on scalar collision)
    - RemoveMessage    — sentinel: signals add_messages to drop a message by id
    - REMOVE_ALL_MESSAGES — sentinel: signals add_messages to clear the list
"""

from typing import Any, Callable, Dict, List

__all__ = [
    "add_messages",
    "dict_merge",
    "RemoveMessage",
    "REMOVE_ALL_MESSAGES",
    "Reducer",
]


Reducer = Callable[[Any, Any], Any]


class _RemoveAllSentinel:
    """Singleton sentinel emitted inside a delta list to clear the cell."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "REMOVE_ALL_MESSAGES"


REMOVE_ALL_MESSAGES = _RemoveAllSentinel()


class RemoveMessage:
    """Sentinel emitted inside a delta list to drop a message by id.

    Usage::

        return {"messages": [RemoveMessage(id="msg_42")]}

    ``add_messages(old, [RemoveMessage(id="msg_42")])`` removes any message in
    ``old`` whose ``id`` matches ``"msg_42"`` and returns the pruned list.
    """

    __slots__ = ("id",)

    def __init__(self, id: str) -> None:
        self.id = id

    def __repr__(self) -> str:
        return f"RemoveMessage(id={self.id!r})"

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, RemoveMessage) and self.id == other.id

    def __hash__(self) -> int:
        return hash(("RemoveMessage", self.id))


def _msg_id(msg: Any) -> Any:
    """Extract id from a message-like value.

    Accepts dicts (``msg["id"]``) or objects with ``.id`` attribute.
    Returns ``None`` when no id is present — message is treated as un-keyed
    and always appended.
    """
    if isinstance(msg, dict):
        return msg.get("id")
    return getattr(msg, "id", None)


def add_messages(old: List[Any], new: List[Any]) -> List[Any]:
    """LangGraph-compatible message-list reducer with id upsert + sentinels.

    Semantics:
        - Both ``old`` and ``new`` are lists.
        - ``REMOVE_ALL_MESSAGES`` anywhere in ``new`` → return ``[]`` immediately.
        - ``RemoveMessage(id=X)`` in ``new`` → drop matching id from ``old``.
        - Message in ``new`` whose id matches one in ``old`` → replace (upsert).
        - Message in ``new`` with a new id or no id → append.

    Args:
        old: current list (may be None or [])
        new: incoming delta list

    Returns:
        Merged list.

    Raises:
        TypeError: if ``old`` or ``new`` is not list-shaped.
    """
    if old is None:
        old = []
    if not isinstance(old, list):
        raise TypeError(f"add_messages expects list, got old={type(old).__name__}")
    if not isinstance(new, list):
        raise TypeError(f"add_messages expects list, got new={type(new).__name__}")

    # Fast path: REMOVE_ALL anywhere in delta clears the list.
    for item in new:
        if isinstance(item, _RemoveAllSentinel):
            return []

    # Index old by id for O(1) upsert lookup.
    result = list(old)
    id_to_pos = {_msg_id(m): i for i, m in enumerate(result) if _msg_id(m) is not None}

    for item in new:
        if isinstance(item, RemoveMessage):
            pos = id_to_pos.pop(item.id, None)
            if pos is not None:
                result[pos] = None  # tombstone; compact at end
            continue

        item_id = _msg_id(item)
        if item_id is not None and item_id in id_to_pos:
            result[id_to_pos[item_id]] = item  # upsert
        else:
            if item_id is not None:
                id_to_pos[item_id] = len(result)
            result.append(item)

    return [m for m in result if m is not None]


def dict_merge(old: Dict[Any, Any], new: Dict[Any, Any]) -> Dict[Any, Any]:
    """Recursive dict merge. Right (new) wins on scalar collision.

    Nested dicts are merged key-wise. Non-dict values are replaced.

    Args:
        old: existing dict (None treated as {})
        new: incoming dict delta

    Returns:
        New merged dict (does not mutate inputs).

    Raises:
        TypeError: if either arg is not a dict.
    """
    if old is None:
        old = {}
    if not isinstance(old, dict):
        raise TypeError(f"dict_merge expects dict, got old={type(old).__name__}")
    if not isinstance(new, dict):
        raise TypeError(f"dict_merge expects dict, got new={type(new).__name__}")

    result = dict(old)
    for k, v in new.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = dict_merge(result[k], v)
        else:
            result[k] = v
    return result
