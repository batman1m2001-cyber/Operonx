"""Keys and stores for ``@op(cache=...)``.

A cached call is found again by a key built from three things:

1. **the graph**: a fingerprint of every op in the root graph (its full
   name and what it computes) and every edge, so two graphs that happen to
   share an engine variable and an op variable never answer each other;
2. **the op**: its full name, its class, and what it computes beyond its
   inputs (``BaseOp._cache_identity``; for a function op, a hash of the
   function's code);
3. **the inputs**, encoded exactly: JSON values, dataclasses, pydantic
   models, sets and bytes, each tagged with its type. Anything else raises,
   because a key made from ``str(value)`` lets two different values that
   print alike share an entry.

Each part is hashed with BLAKE2b, so a key is stable across processes and
a file-backed cache can be reloaded. Each store is a bounded LRU.

Before 1.15 the key was ``op.full_name`` plus an FNV hash of
``json.dumps(inputs, default=str)``. The full name is spelled from
variable names, so two different graphs built under ``engine = Operon(...)``
with an op bound to the same name returned each other's results
(``docs/roadmap/evidence/probes/p2_interrupt_cache_ckpt.py``, P6).
"""

from __future__ import annotations

import dataclasses
import hashlib
import struct
from collections import OrderedDict
from pathlib import Path
from types import CodeType
from typing import TYPE_CHECKING, Any, Dict, Iterator, Optional

import orjson

from operonx.core.loggings import LOGGER

if TYPE_CHECKING:
    from operonx.core.ops.base import BaseOp

#: Entries kept per store before the least recently used one is dropped.
#: One store per cached op (or per file, for ``cache="path"``).
CACHE_MAX_ENTRIES = 1024

#: Bytes in every digest: the op scope and the entry key.
DIGEST_SIZE = 16

#: First bytes of a cache file. A file without them was written by an
#: older operonx with a key that is no longer computed.
FILE_MAGIC = b"OXCACHE2"

_ORJSON_OPTIONS = orjson.OPT_SORT_KEYS | orjson.OPT_PASSTHROUGH_DATACLASS


#: What ``CacheStore.get`` returns for a key it does not hold.
MISS = object()


class CacheKeyError(TypeError):
    """A cached op met a value that has no exact encoding."""


# ── Hashing ────────────────────────────────────────────────────────────


def _blake(*parts: bytes) -> bytes:
    h = hashlib.blake2b(digest_size=DIGEST_SIZE)
    for part in parts:
        # Length-prefixed, so ("ab", "c") and ("a", "bc") differ.
        h.update(struct.pack("<Q", len(part)))
        h.update(part)
    return h.digest()


def _tagged(obj: Any) -> Any:
    """orjson ``default``: the types with an exact encoding, tagged.

    Never ``str(obj)``: two values that print alike would share a key.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        cls = type(obj)
        fields = {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}
        return {"$type": f"{cls.__module__}.{cls.__qualname__}", "fields": fields}
    model_dump = getattr(obj, "model_dump", None)
    if model_dump is not None and callable(model_dump):
        cls = type(obj)
        return {"$type": f"{cls.__module__}.{cls.__qualname__}", "fields": model_dump()}
    if isinstance(obj, (set, frozenset)):
        return {"$set": sorted(_encode(item).decode() for item in obj)}
    if isinstance(obj, (bytes, bytearray)):
        return {"$bytes": bytes(obj).hex()}
    raise TypeError(f"Type is not JSON serializable: {type(obj).__qualname__}")


def _encode(value: Any) -> bytes:
    return orjson.dumps(value, default=_tagged, option=_ORJSON_OPTIONS)


def _code_parts(code: CodeType) -> Iterator[bytes]:
    yield code.co_code
    yield repr((code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars)).encode()
    for const in code.co_consts:
        yield from _const_parts(const)


def _const_parts(const: Any) -> Iterator[bytes]:
    if isinstance(const, CodeType):
        yield b"code"
        yield from _code_parts(const)
    elif isinstance(const, tuple):
        yield b"tuple%d" % len(const)
        for item in const:
            yield from _const_parts(item)
    elif isinstance(const, frozenset):
        # Iteration order follows the string hash seed; sort to be stable.
        yield b"frozenset%d" % len(const)
        yield from sorted(b"".join(_const_parts(item)) for item in const)
    else:
        yield f"{type(const).__name__}:{const!r}".encode()


def code_digest(fn: Any) -> bytes:
    """Hash of a function's code: bytecode, names and constants.

    Stable across processes (no file name, line numbers or memory
    addresses), so editing a function's body starts a fresh cache while
    moving it in its file does not. The values of the globals and
    closure cells it reads are not part of it.
    """
    code = getattr(fn, "__code__", None)
    if code is None:
        raise CacheKeyError(f"{fn!r} has no __code__ to hash")
    return _blake(*_code_parts(code))


def _walk(op: "BaseOp") -> Iterator["BaseOp"]:
    yield op
    for child in (getattr(op, "_ops", None) or {}).values():
        yield from _walk(child)


def _identity_bytes(op: "BaseOp") -> bytes:
    try:
        return _encode(op._cache_identity())
    except TypeError as exc:
        raise CacheKeyError(
            f"op '{op.full_name}' ({type(op).__name__}) cannot be part of a cache key: "
            f"its _cache_identity() has no exact encoding ({exc}). A cached op keys on "
            "every op in its graph, each described as JSON values; override "
            f"_cache_identity() on {type(op).__name__} to return them."
        ) from None


def graph_fingerprint(op: "BaseOp") -> bytes:
    """Digest of the root graph that ``op`` belongs to.

    Covers every op's full name and identity and every edge, so changing
    any op in the graph starts a fresh cache for all of its cached ops.
    """
    root = op
    while root.parent is not None:
        root = root.parent
    parts = []
    for node in sorted(_walk(root), key=lambda n: n.full_name):
        parts.append(node.full_name.encode())
        parts.append(_identity_bytes(node))
        edges = getattr(node, "_edges", None)
        if edges:
            parts.append(
                _encode(sorted([src, dst, bool(e.soft)] for (src, dst), e in edges.items()))
            )
    return _blake(*parts)


def op_scope(op: "BaseOp") -> bytes:
    """The part of every key that is the same for all of ``op``'s calls."""
    return _blake(graph_fingerprint(op), op.full_name.encode(), _identity_bytes(op))


def entry_key(op: "BaseOp", scope: bytes, inputs: Dict[str, Any]) -> bytes:
    """The key of one call: the op's scope and its inputs, exactly."""
    try:
        encoded = _encode(inputs)
    except TypeError:
        for name, value in inputs.items():
            try:
                _encode(value)
            except TypeError as exc:
                raise CacheKeyError(
                    f"op '{op.full_name}' has cache= set, and its input '{name}' has no "
                    f"exact encoding to key the cache on ({exc}). Cacheable inputs are JSON "
                    "values (dict keys must be str), dataclasses, pydantic models, sets and "
                    "bytes. Pass the input as one of those, or remove cache= from this op."
                ) from None
        raise
    return _blake(scope, encoded)


# ── Stores ─────────────────────────────────────────────────────────────


class CacheStore:
    """A bounded LRU of ``{key: outputs}``, optionally backed by a file."""

    __slots__ = ("path", "entries")

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.entries: "OrderedDict[bytes, Any]" = OrderedDict()
        if path:
            self._load(Path(path))

    def get(self, key: bytes) -> Any:
        """The stored outputs, or ``MISS``. A hit becomes the newest entry."""
        value = self.entries.get(key, MISS)
        if value is not MISS:
            self.entries.move_to_end(key)
        return value

    def put(self, key: bytes, value: Any) -> None:
        self.entries[key] = value
        self.entries.move_to_end(key)
        while len(self.entries) > CACHE_MAX_ENTRIES:
            self.entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self.entries)

    # File layout: FILE_MAGIC, u64 count, then per entry the DIGEST_SIZE
    # key, a u32 length and that many bytes of JSON.

    def _load(self, p: Path) -> None:
        if not p.exists():
            return
        data = p.read_bytes()
        if not data.startswith(FILE_MAGIC):
            LOGGER.warning(
                "op cache file %s was written by an older operonx, whose cache key is no "
                "longer used; starting it empty (the next save overwrites it)",
                p,
            )
            return
        offset = len(FILE_MAGIC)
        (count,) = struct.unpack_from("<Q", data, offset)
        offset += 8
        head = DIGEST_SIZE + 4
        for i in range(count):
            if offset + head > len(data):
                LOGGER.warning("op cache file %s is truncated after %d of %d entries", p, i, count)
                return
            key = data[offset : offset + DIGEST_SIZE]
            (size,) = struct.unpack_from("<I", data, offset + DIGEST_SIZE)
            offset += head
            if offset + size > len(data):
                LOGGER.warning("op cache file %s is truncated after %d of %d entries", p, i, count)
                return
            self.put(key, orjson.loads(data[offset : offset + size]))
            offset += size

    def save(self) -> int:
        """Write the store to its file. Returns the number of entries written."""
        if not self.path:
            return 0
        chunks = [FILE_MAGIC, struct.pack("<Q", len(self.entries))]
        for key, value in self.entries.items():
            try:
                body = orjson.dumps(value)
            except TypeError as exc:
                raise CacheKeyError(
                    f"op cache file {self.path}: an op's outputs hold a value orjson cannot "
                    f"write ({exc}). A file-backed cache stores outputs as JSON; return JSON "
                    "values from the op, or use cache=True to keep the cache in memory."
                ) from None
            chunks.append(key + struct.pack("<I", len(body)))
            chunks.append(body)
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"".join(chunks))
        return len(self.entries)
