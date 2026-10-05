"""How a journal writes values: JSON, with tags for what JSON cannot say.

A resumed run must get back exactly what an op returned — a tuple stays a
tuple, a dataclass stays that dataclass — or it would differ from the run
that crashed. Pickle gives that, and also runs code when it loads: whoever
can write a journal (a shared SQLite file, a Postgres table, R4) could then
run anything in every worker that resumes from it. So the journal is JSON,
and the few types JSON lacks are written as a tagged object::

    {"$t": "tuple", "v": [...]}
    {"$t": "dc", "cls": "pkg.mod:Order", "v": {...}}   # a dataclass

A class named in a journal is rebuilt only when it is a dataclass, a
pydantic model or an enum — imported by name, filled field by field
without calling its ``__init__`` — never anything else. A value of any
other type cannot be journalled, and says so where it was written.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import decimal
import enum
import hashlib
import importlib
import json
import uuid
from pathlib import PurePath, PurePosixPath
from typing import Any

__all__ = ["CodecError", "decode", "digest", "dumps", "encode", "loads"]

_TAG = "$t"


class CodecError(TypeError):
    """A value the journal has no way to write."""


def _path(cls: type) -> str:
    return f"{cls.__module__}:{cls.__qualname__}"


def encode(value: Any) -> Any:
    """*value* as plain JSON data (tagged where JSON has no word for it)."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, enum.Enum):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value) and _TAG not in value:
            return {k: encode(v) for k, v in value.items()}
        return {_TAG: "dict", "v": [[encode(k), encode(v)] for k, v in value.items()]}
    if isinstance(value, tuple) and not hasattr(value, "_fields"):
        return {_TAG: "tuple", "v": [encode(v) for v in value]}
    if isinstance(value, enum.Enum):
        return {_TAG: "enum", "cls": _path(type(value)), "v": encode(value.value)}
    if isinstance(value, (set, frozenset)):
        tag = "set" if isinstance(value, set) else "frozenset"
        return {_TAG: tag, "v": [encode(v) for v in value]}
    if isinstance(value, (bytes, bytearray)):
        return {_TAG: "bytes", "v": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dt.datetime):
        return {_TAG: "datetime", "v": value.isoformat()}
    if isinstance(value, dt.date):
        return {_TAG: "date", "v": value.isoformat()}
    if isinstance(value, dt.time):
        return {_TAG: "time", "v": value.isoformat()}
    if isinstance(value, dt.timedelta):
        return {_TAG: "timedelta", "v": [value.days, value.seconds, value.microseconds]}
    if isinstance(value, decimal.Decimal):
        return {_TAG: "decimal", "v": str(value)}
    if isinstance(value, uuid.UUID):
        return {_TAG: "uuid", "v": str(value)}
    if isinstance(value, PurePath):
        return {_TAG: "path", "v": value.as_posix()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
        return {_TAG: "dc", "cls": _path(type(value)), "v": fields}
    if _is_model(type(value)):
        fields = {name: encode(getattr(value, name)) for name in type(value).model_fields}
        return {_TAG: "model", "cls": _path(type(value)), "v": fields}
    if isinstance(value, tuple):  # a namedtuple
        return {_TAG: "namedtuple", "cls": _path(type(value)), "v": [encode(v) for v in value]}
    from operonx.core.ops._events import SELF_CTX

    if value is SELF_CTX:
        return {_TAG: "self_ctx"}
    raise CodecError(
        f"a {type(value).__name__} cannot be journalled: a durable run's values are plain "
        "data (str, numbers, lists, dicts, tuples, sets, bytes, dates, Decimal, UUID) or "
        "dataclasses, pydantic models and enums of them"
    )


def _is_model(cls: type) -> bool:
    try:
        from pydantic import BaseModel
    except ImportError:  # pragma: no cover - pydantic is a dependency
        return False
    return isinstance(cls, type) and issubclass(cls, BaseModel)


def _class(path: str, kind: str) -> type:
    """The class a journal names — only a kind it may rebuild."""
    module, _, qual = path.partition(":")
    try:
        target: Any = importlib.import_module(module)
        for part in qual.split("."):
            target = getattr(target, part)
    except (ImportError, AttributeError) as exc:
        raise CodecError(f"the journal names {path}, which this process cannot import") from exc
    ok = isinstance(target, type) and (
        (kind == "dc" and dataclasses.is_dataclass(target))
        or (kind == "model" and _is_model(target))
        or (kind == "enum" and issubclass(target, enum.Enum))
        or (kind == "namedtuple" and issubclass(target, tuple) and hasattr(target, "_fields"))
    )
    if not ok:
        raise CodecError(f"the journal names {path} as a {kind}, and it is not one")
    return target


def decode(data: Any) -> Any:
    """What :func:`encode` was given."""
    if isinstance(data, list):
        return [decode(v) for v in data]
    if not isinstance(data, dict):
        return data
    tag = data.get(_TAG)
    if tag is None:
        return {k: decode(v) for k, v in data.items()}
    v = data.get("v")
    if tag == "tuple":
        return tuple(decode(x) for x in v)
    if tag == "dict":
        return {decode(k): decode(x) for k, x in v}
    if tag == "set":
        return {decode(x) for x in v}
    if tag == "frozenset":
        return frozenset(decode(x) for x in v)
    if tag == "bytes":
        return base64.b64decode(v)
    if tag == "datetime":
        return dt.datetime.fromisoformat(v)
    if tag == "date":
        return dt.date.fromisoformat(v)
    if tag == "time":
        return dt.time.fromisoformat(v)
    if tag == "timedelta":
        return dt.timedelta(days=v[0], seconds=v[1], microseconds=v[2])
    if tag == "decimal":
        return decimal.Decimal(v)
    if tag == "uuid":
        return uuid.UUID(v)
    if tag == "path":
        return PurePosixPath(v)
    if tag == "enum":
        return _class(data["cls"], "enum")(decode(v))
    if tag == "namedtuple":
        return _class(data["cls"], "namedtuple")(*(decode(x) for x in v))
    if tag == "dc":
        cls = _class(data["cls"], "dc")
        obj = object.__new__(cls)
        for name, x in v.items():  # field by field: no __init__ runs
            object.__setattr__(obj, name, decode(x))
        return obj
    if tag == "model":
        cls = _class(data["cls"], "model")
        return cls.model_construct(**{name: decode(x) for name, x in v.items()})
    if tag == "self_ctx":
        from operonx.core.ops._events import SELF_CTX

        return SELF_CTX
    raise CodecError(f"the journal holds an unknown tag {tag!r}")


def dumps(value: Any) -> bytes:
    """*value* as the journal stores it (UTF-8 JSON)."""
    return json.dumps(encode(value), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def loads(blob: Any) -> Any:
    if isinstance(blob, memoryview):
        blob = bytes(blob)
    return decode(json.loads(blob))


def digest(value: Any) -> str:
    """A value's fingerprint: its encoding with keys sorted."""
    text = json.dumps(encode(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()
