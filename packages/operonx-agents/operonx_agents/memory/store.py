"""Long-term memory: a few typed items per owner, each with its source, confidence and expiry.

Not a vector store and not a transcript: an item is a short sentence worth
carrying from one conversation to the next — a preference, a goal, a fact,
a constraint — that the person it is about can list, correct and delete.
A session (:mod:`operonx_agents.context.session`) is what was said; a memory
item is what is kept of it.

Every call names its ``owner``; a store never answers for another one. An
item's ``source`` says where it came from (a conversation and a message,
say) so a page can show it. ``expires`` is a time after which the item no
longer counts and :meth:`MemoryStore.purge` deletes it.

Adding an item an owner already has (same kind, same text up to case,
accents and spacing) returns that item, refreshed, instead of a copy.

A :class:`Codec` seals the text at rest: ``encode(text, place)`` before a
write and ``decode`` after a read, with the item's id in ``place`` so a
sealed text moved to another row does not open there.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Union, runtime_checkable

from operonx_agents._storage import SQLite

__all__ = [
    "KINDS",
    "Codec",
    "InMemoryMemoryStore",
    "MemoryItem",
    "MemoryStore",
    "SQLiteMemoryStore",
    "fold",
    "search",
]

KINDS = ("preference", "goal", "fact", "constraint")

_KEEP: Any = object()


@dataclass
class MemoryItem:
    """One thing remembered about ``owner``."""

    id: str
    owner: str
    kind: str
    text: str
    source: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    created: float = 0.0
    updated: float = 0.0
    expires: Optional[float] = None

    def active(self, now: Optional[float] = None) -> bool:
        return self.expires is None or self.expires > (time.time() if now is None else now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def fold(text: str) -> str:
    """Case, accents and spacing folded: how two texts are compared."""
    t = unicodedata.normalize("NFD", str(text).replace("đ", "d").replace("Đ", "D"))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t).strip().casefold()


@runtime_checkable
class Codec(Protocol):
    def encode(self, text: str, place: str) -> str: ...

    def decode(self, text: str, place: str) -> str: ...


@runtime_checkable
class MemoryStore(Protocol):
    """Where an owner's items live. Every method is scoped to one owner,
    but :meth:`purge`, which sweeps expired items for all of them."""

    async def add(
        self,
        owner: str,
        kind: str,
        text: str,
        *,
        source: Optional[Dict[str, Any]] = None,
        confidence: float = 1.0,
        expires: Optional[float] = None,
    ) -> MemoryItem: ...

    async def get(self, owner: str, item_id: str) -> Optional[MemoryItem]: ...

    async def items(
        self,
        owner: str,
        *,
        kinds: Optional[Iterable[str]] = None,
        active: bool = True,
        limit: Optional[int] = None,
    ) -> List[MemoryItem]: ...

    async def update(
        self,
        owner: str,
        item_id: str,
        *,
        text: Optional[str] = None,
        kind: Optional[str] = None,
        confidence: Optional[float] = None,
        expires: Any = _KEEP,
    ) -> Optional[MemoryItem]: ...

    async def delete(self, owner: str, item_id: str) -> bool: ...

    async def clear(self, owner: str) -> int: ...

    async def purge(self, now: Optional[float] = None) -> int: ...


def _checked(kind: str, text: str) -> tuple:
    kind, text = str(kind or "").strip(), " ".join(str(text or "").split())
    if not kind:
        raise ValueError("A memory item needs a kind.")
    if not text:
        raise ValueError("A memory item needs a text.")
    return kind, text


async def search(
    store: MemoryStore, owner: str, query: str, *, limit: int = 10
) -> List[MemoryItem]:
    """``owner``'s active items that share words with ``query`` (folded),
    the most shared first, newest first among equals. An empty query lists
    them, newest first. A scan of one owner's items: they are few."""
    have = await store.items(owner)
    words = set(fold(query).split())
    if not words:
        return have[:limit]
    scored = [(len(words & set(fold(i.text).split())), i) for i in have]
    scored = [(n, i) for n, i in scored if n]
    scored.sort(key=lambda x: (-x[0], -x[1].updated))
    return [i for _, i in scored[:limit]]


class InMemoryMemoryStore:
    """Items in a dict: tests, and processes that need nothing kept."""

    def __init__(self) -> None:
        self._items: Dict[str, MemoryItem] = {}

    async def add(
        self, owner, kind, text, *, source=None, confidence=1.0, expires=None
    ) -> MemoryItem:
        kind, text = _checked(kind, text)
        now = time.time()
        for i in self._items.values():
            if i.owner == owner and i.kind == kind and fold(i.text) == fold(text) and i.active(now):
                i.updated, i.confidence = now, max(i.confidence, confidence)
                return MemoryItem(**i.to_dict())
        item = MemoryItem(
            uuid.uuid4().hex, owner, kind, text, dict(source or {}), confidence, now, now, expires
        )
        self._items[item.id] = item
        return MemoryItem(**item.to_dict())

    async def get(self, owner, item_id) -> Optional[MemoryItem]:
        i = self._items.get(item_id)
        return MemoryItem(**i.to_dict()) if i is not None and i.owner == owner else None

    async def items(self, owner, *, kinds=None, active=True, limit=None) -> List[MemoryItem]:
        want = set(kinds) if kinds is not None else None
        now = time.time()
        out = [
            MemoryItem(**i.to_dict())
            for i in self._items.values()
            if i.owner == owner
            and (want is None or i.kind in want)
            and (not active or i.active(now))
        ]
        out.sort(key=lambda i: -i.updated)
        return out[:limit] if limit else out

    async def update(self, owner, item_id, *, text=None, kind=None, confidence=None, expires=_KEEP):
        i = self._items.get(item_id)
        if i is None or i.owner != owner:
            return None
        if text is not None or kind is not None:
            i.kind, i.text = _checked(
                i.kind if kind is None else kind, i.text if text is None else text
            )
        if confidence is not None:
            i.confidence = confidence
        if expires is not _KEEP:
            i.expires = expires
        i.updated = time.time()
        return MemoryItem(**i.to_dict())

    async def delete(self, owner, item_id) -> bool:
        i = self._items.get(item_id)
        if i is None or i.owner != owner:
            return False
        del self._items[item_id]
        return True

    async def clear(self, owner) -> int:
        gone = [k for k, i in self._items.items() if i.owner == owner]
        for k in gone:
            del self._items[k]
        return len(gone)

    async def purge(self, now=None) -> int:
        now = time.time() if now is None else now
        gone = [k for k, i in self._items.items() if not i.active(now)]
        for k in gone:
            del self._items[k]
        return len(gone)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS operonx_agents_memory (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    expires REAL
);
CREATE INDEX IF NOT EXISTS operonx_agents_memory_owner ON operonx_agents_memory (owner, updated);
"""
_COLUMNS = "id, owner, kind, text, source, confidence, created, updated, expires"


class SQLiteMemoryStore:
    """One row per item in a SQLite file (created if missing), on the
    connection the file's sessions and stores share. With a ``codec`` the
    text is stored encoded; duplicates are then found by decoding the
    owner's items, which are few."""

    def __init__(self, path: Union[str, Path], codec: Optional[Codec] = None) -> None:
        self._db = SQLite(path, _SCHEMA)
        self._codec = codec

    def _place(self, item_id: str) -> str:
        return f"operonx_agents_memory/{item_id}/text"

    def _row(self, r) -> MemoryItem:
        text = r[3] if self._codec is None else self._codec.decode(r[3], self._place(r[0]))
        return MemoryItem(r[0], r[1], r[2], text, json.loads(r[4]), r[5], r[6], r[7], r[8])

    async def _all(self, owner: str) -> List[MemoryItem]:
        rows = await self._db.run(
            lambda db: db.execute(
                f"SELECT {_COLUMNS} FROM operonx_agents_memory WHERE owner = ? "
                "ORDER BY updated DESC",
                (owner,),
            ).fetchall(),
            write=False,
        )
        return [self._row(r) for r in rows]

    async def add(
        self, owner, kind, text, *, source=None, confidence=1.0, expires=None
    ) -> MemoryItem:
        kind, text = _checked(kind, text)
        now = time.time()
        item = MemoryItem(
            uuid.uuid4().hex, owner, kind, text, dict(source or {}), confidence, now, now, expires
        )
        stored = text if self._codec is None else self._codec.encode(text, self._place(item.id))

        def write(db) -> MemoryItem:  # the duplicate check and the write in one transaction
            rows = db.execute(
                f"SELECT {_COLUMNS} FROM operonx_agents_memory WHERE owner = ?", (owner,)
            ).fetchall()
            for r in rows:
                i = self._row(r)
                if i.kind == kind and fold(i.text) == fold(text) and i.active(now):
                    i.updated, i.confidence = now, max(i.confidence, confidence)
                    db.execute(
                        "UPDATE operonx_agents_memory SET updated = ?, confidence = ? WHERE id = ?",
                        (now, i.confidence, i.id),
                    )
                    return i
            db.execute(
                f"INSERT INTO operonx_agents_memory ({_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.id,
                    owner,
                    kind,
                    stored,
                    json.dumps(item.source, ensure_ascii=False, default=str),
                    confidence,
                    now,
                    now,
                    expires,
                ),
            )
            return item

        return await self._db.run(write)

    async def get(self, owner, item_id) -> Optional[MemoryItem]:
        r = await self._db.run(
            lambda db: db.execute(
                f"SELECT {_COLUMNS} FROM operonx_agents_memory WHERE id = ? AND owner = ?",
                (item_id, owner),
            ).fetchone(),
            write=False,
        )
        return None if r is None else self._row(r)

    async def items(self, owner, *, kinds=None, active=True, limit=None) -> List[MemoryItem]:
        want = set(kinds) if kinds is not None else None
        now = time.time()
        out = [
            i
            for i in await self._all(owner)
            if (want is None or i.kind in want) and (not active or i.active(now))
        ]
        return out[:limit] if limit else out

    async def update(self, owner, item_id, *, text=None, kind=None, confidence=None, expires=_KEEP):
        i = await self.get(owner, item_id)
        if i is None:
            return None
        if text is not None or kind is not None:
            i.kind, i.text = _checked(
                i.kind if kind is None else kind, i.text if text is None else text
            )
        if confidence is not None:
            i.confidence = confidence
        if expires is not _KEEP:
            i.expires = expires
        i.updated = time.time()
        stored = i.text if self._codec is None else self._codec.encode(i.text, self._place(i.id))
        await self._db.run(
            lambda db: db.execute(
                "UPDATE operonx_agents_memory SET kind = ?, text = ?, confidence = ?, expires = ?, "
                "updated = ? "
                "WHERE id = ? AND owner = ?",
                (i.kind, stored, i.confidence, i.expires, i.updated, i.id, owner),
            )
        )
        return i

    async def delete(self, owner, item_id) -> bool:
        cur = await self._db.run(
            lambda db: db.execute(
                "DELETE FROM operonx_agents_memory WHERE id = ? AND owner = ?", (item_id, owner)
            )
        )
        return cur.rowcount > 0

    async def clear(self, owner) -> int:
        cur = await self._db.run(
            lambda db: db.execute("DELETE FROM operonx_agents_memory WHERE owner = ?", (owner,))
        )
        return cur.rowcount

    async def purge(self, now=None) -> int:
        now = time.time() if now is None else now
        cur = await self._db.run(
            lambda db: db.execute(
                "DELETE FROM operonx_agents_memory WHERE expires IS NOT NULL AND expires <= ?",
                (now,),
            )
        )
        return cur.rowcount

    def close(self) -> None:
        self._db.close()
