"""``KBFilter``: the closed filter vocabulary (track5 §12.3, PLAN R3/R4).

This is not a portable query language. It is a fixed set of conditions over
fields the KB itself writes into every index entry (the *payload*), so each
backend's compiler (:mod:`operonx_kb.retrieval.filters`) is small and fully
tested, and :func:`matches` is the reference every compiler is checked against.

Semantics, identical everywhere:

- Every condition is AND-ed, and the collection is always one of them.
- ``document_ids``, ``mime_in``: the entry's value is one of the list.
- ``tags_any``, ``acl_any``: the entry's list shares at least one value. A
  document with no ACL never matches ``acl_any`` (closed by default).
- ``tags_all``: the entry's list holds every value.
- ``created_after`` is inclusive, ``created_before`` exclusive (UTC; a naive
  time is read as UTC).
- ``fields``: only names the collection declares ``filterable``. A scalar is
  equality (for a ``keyword[]`` field: the list contains it); a list is "any
  of" (for a ``keyword[]`` field: the lists overlap). A document without the
  field never matches.

An empty list is refused rather than read as "no condition": it would either
match nothing or, worse, everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from operonx_kb.errors import FilterError
from operonx_kb.model.collection import CollectionSpec, FieldType

__all__ = [
    "KBFilter",
    "CheckedFilter",
    "PAYLOAD_KEYS",
    "field_key",
    "index_payload",
    "matches",
    "epoch",
]

#: The payload every index entry carries, besides ``kb_f_<name>`` fields.
PAYLOAD_KEYS = ("kb_collection", "kb_document", "kb_tags", "kb_acl", "kb_mime", "kb_created")

Scalar = Union[str, int, float, bool]


def field_key(name: str) -> str:
    """The payload key (and index column) of a declared field."""
    return f"kb_f_{name}"


def epoch(value: Union[datetime, str]) -> float:
    """Seconds since the epoch, UTC; a naive time is read as UTC."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


class KBFilter(BaseModel):
    """Which documents a query may see, besides the collection.

    Attributes:
        document_ids: Only these documents.
        tags_any: Documents with at least one of these tags.
        tags_all: Documents with every one of these tags.
        acl_any: The caller's principals; documents whose ACL holds one of them.
        mime_in: Only these MIME types.
        created_after: Documents first ingested at or after this time.
        created_before: Documents first ingested before this time.
        fields: Conditions on declared ``filterable`` fields.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_ids: Optional[List[str]] = None
    tags_any: Optional[List[str]] = None
    tags_all: Optional[List[str]] = None
    acl_any: Optional[List[str]] = None
    mime_in: Optional[List[str]] = None
    created_after: Optional[datetime] = None
    created_before: Optional[datetime] = None
    fields: Dict[str, Union[Scalar, List[Scalar]]] = Field(default_factory=dict)

    @field_validator("document_ids", "tags_any", "tags_all", "acl_any", "mime_in")
    @classmethod
    def _not_empty(cls, value: Optional[List[str]], info) -> Optional[List[str]]:
        if value is not None and not value:
            raise ValueError(
                f"{info.field_name}=[] is an empty condition; omit it to not filter on it"
            )
        return value

    @field_validator("fields")
    @classmethod
    def _no_empty_lists(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        empty = sorted(k for k, v in value.items() if isinstance(v, list) and not v)
        if empty:
            raise ValueError(f"fields {empty} hold an empty list; omit them to not filter on them")
        return value

    @classmethod
    def of(cls, value: Union["KBFilter", Mapping[str, Any], None]) -> "KBFilter":
        """A filter from a model, a dict (a graph input, JSON) or ``None`` (no condition)."""
        if value is None:
            return cls()
        return value if isinstance(value, KBFilter) else cls.model_validate(dict(value))

    def checked(self, spec: CollectionSpec) -> "CheckedFilter":
        """This filter against a collection: fields declared and typed, values normalised.

        Raises:
            FilterError: A field is not declared ``filterable``, or a value has
                the wrong type for it.
        """
        types: Dict[str, FieldType] = {}
        values: Dict[str, Union[Scalar, List[Scalar]]] = {}
        for name, value in self.fields.items():
            if name not in spec.filterable:
                raise FilterError(
                    f"field {name!r} is not filterable in this collection; declare it in "
                    "CollectionSpec(filterable={...}) and re-ingest so the indexes carry it",
                    {"declared": sorted(spec.filterable)},
                )
            kind = spec.filterable[name]
            types[name] = kind
            items = value if isinstance(value, list) else [value]
            normal = [_coerce(kind, v, name) for v in items]
            values[name] = normal if isinstance(value, list) else normal[0]
        return CheckedFilter(self, types, values)


@dataclass(frozen=True)
class CheckedFilter:
    """A :class:`KBFilter` validated against a collection: what compilers consume.

    Attributes:
        filter: The filter.
        types: The declared type of each field the filter names.
        fields: Its field values, normalised (a datetime is epoch seconds).
    """

    filter: KBFilter
    types: Dict[str, FieldType]
    fields: Dict[str, Union[Scalar, List[Scalar]]]

    @property
    def created_after(self) -> Optional[float]:
        return epoch(self.filter.created_after) if self.filter.created_after else None

    @property
    def created_before(self) -> Optional[float]:
        return epoch(self.filter.created_before) if self.filter.created_before else None


def _coerce(kind: FieldType, value: Any, name: str) -> Scalar:
    ok = {
        "keyword": isinstance(value, str),
        "keyword[]": isinstance(value, str),
        "int": isinstance(value, int) and not isinstance(value, bool),
        "float": isinstance(value, (int, float)) and not isinstance(value, bool),
        "bool": isinstance(value, bool),
        "datetime": isinstance(value, (str, datetime)),
    }[kind]
    if not ok:
        raise FilterError(
            f"field {name!r} is declared {kind!r}; {value!r} is a {type(value).__name__}"
        )
    if kind == "datetime":
        try:
            return epoch(value)
        except ValueError as exc:
            raise FilterError(f"field {name!r}: {value!r} is not an ISO-8601 time") from exc
    return float(value) if kind == "float" else value


def index_payload(
    *,
    spec: CollectionSpec,
    collection_id: str,
    document_id: str,
    tags: Sequence[str],
    acl: Sequence[str],
    mime: str,
    created_at: Union[datetime, str],
    metadata: Mapping[str, Any],
) -> Dict[str, Any]:
    """The filter payload of every index entry of one document (PLAN R3).

    Raises:
        FilterError: A declared field's metadata value has the wrong type.
    """
    out: Dict[str, Any] = {
        "kb_collection": collection_id,
        "kb_document": document_id,
        "kb_tags": list(tags),
        "kb_acl": list(acl),
        "kb_mime": mime,
        "kb_created": epoch(created_at),
    }
    for name, kind in spec.filterable.items():
        value = metadata.get(name)
        if value is None:
            out[field_key(name)] = None
        elif kind == "keyword[]":
            items = value if isinstance(value, list) else [value]
            out[field_key(name)] = [_coerce(kind, v, name) for v in items]
        else:
            out[field_key(name)] = _coerce(kind, value, name)
    return out


def _any(have: Any, wanted: Sequence[Any]) -> bool:
    items = have if isinstance(have, list) else [have]
    return any(v in wanted for v in items if v is not None)


def matches(checked: CheckedFilter, collection_id: str, payload: Mapping[str, Any]) -> bool:
    """Whether an entry with ``payload`` passes the filter in ``collection_id``.

    The reference semantics: the catalog's hydration gate and the FAISS
    post-filter use it, and every backend compiler is tested against it.
    """
    f = checked.filter
    if payload.get("kb_collection") != collection_id:
        return False
    if f.document_ids is not None and payload.get("kb_document") not in f.document_ids:
        return False
    tags = payload.get("kb_tags") or []
    if f.tags_any is not None and not set(tags) & set(f.tags_any):
        return False
    if f.tags_all is not None and not set(f.tags_all) <= set(tags):
        return False
    if f.acl_any is not None and not set(payload.get("kb_acl") or []) & set(f.acl_any):
        return False
    if f.mime_in is not None and payload.get("kb_mime") not in f.mime_in:
        return False
    created = payload.get("kb_created")
    if checked.created_after is not None and not (
        created is not None and created >= checked.created_after
    ):
        return False
    if checked.created_before is not None and not (
        created is not None and created < checked.created_before
    ):
        return False
    for name, wanted in checked.fields.items():
        have = payload.get(field_key(name))
        if have is None or not _any(have, wanted if isinstance(wanted, list) else [wanted]):
            return False
    return True
