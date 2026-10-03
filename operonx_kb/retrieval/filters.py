"""``KBFilter`` compiled per backend (track5 §12.3, PLAN R4).

Each compiler turns a :class:`~operonx_kb.model.filter.CheckedFilter` plus the
collection into one backend's **native** filter, with exactly the semantics of
:func:`~operonx_kb.model.filter.matches`:

=================  =============================================================
backend            native filter
=================  =============================================================
FAISS              none: it stores no payload. The retriever post-filters its
                   hits against the catalog (:class:`PostFilter`).
pgvector           ``{"where": sql, "params": {...}}``; declared fields are
                   columns ``kb_f_<name>``.
Qdrant             a condition tree ``{"must": [...]}``.
SQLite FTS5        ``(where_sql, params)``; lists and declared fields are JSON.
Postgres FTS       the pgvector dialect; declared fields are ``kb_fields`` jsonb.
=================  =============================================================

:func:`native_filter` picks the compiler from the store or index object. A
backend it does not know raises :class:`~operonx_kb.errors.FilterError`: an
unfiltered search of a shared index would read other collections' documents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from operonx_kb.errors import FilterError
from operonx_kb.model.filter import CheckedFilter, field_key

__all__ = [
    "PostFilter",
    "native_filter",
    "compile_pgvector",
    "compile_postgres_fts",
    "compile_qdrant",
    "compile_sqlite_fts",
]


@dataclass(frozen=True)
class PostFilter:
    """The backend cannot filter: search unfiltered, then keep what the catalog says matches."""

    reason: str


# ── Postgres (pgvector and FTS) ──────────────────────────────────────────────


class _Params:
    def __init__(self) -> None:
        self.values: Dict[str, Any] = {}

    def __call__(self, value: Any) -> str:
        name = f"kbf{len(self.values)}"
        self.values[name] = value
        return f"%({name})s"


def _postgres(
    checked: CheckedFilter, collection_id: str, field_sql: Callable[[str, str, Any, _Params], str]
) -> Dict[str, Any]:
    f, p = checked.filter, _Params()
    where = [f"kb_collection = {p(collection_id)}"]
    if f.document_ids is not None:
        where.append(f"kb_document = ANY({p(list(f.document_ids))})")
    if f.tags_any is not None:
        where.append(f"kb_tags && {p(list(f.tags_any))}::text[]")
    if f.tags_all is not None:
        where.append(f"kb_tags @> {p(list(f.tags_all))}::text[]")
    if f.acl_any is not None:
        where.append(f"kb_acl && {p(list(f.acl_any))}::text[]")
    if f.mime_in is not None:
        where.append(f"kb_mime = ANY({p(list(f.mime_in))})")
    if checked.created_after is not None:
        where.append(f"kb_created >= {p(checked.created_after)}")
    if checked.created_before is not None:
        where.append(f"kb_created < {p(checked.created_before)}")
    for name, value in checked.fields.items():
        where.append(field_sql(name, checked.types[name], value, p))
    return {"where": " AND ".join(where), "params": p.values}


def _pg_column(name: str, kind: str, value: Any, p: _Params) -> str:
    column = field_key(name)
    if kind == "keyword[]":
        if isinstance(value, list):
            return f"{column} && {p(value)}::text[]"
        return f"{p(value)} = ANY({column})"
    if isinstance(value, list):
        return f"{column} = ANY({p(value)})"
    return f"{column} = {p(value)}"


_JSON_CAST = {
    "keyword": "",
    "int": "::double precision",
    "float": "::double precision",
    "datetime": "::double precision",
    "bool": "::boolean",
}


def _pg_json(name: str, kind: str, value: Any, p: _Params) -> str:
    if kind == "keyword[]":
        values = value if isinstance(value, list) else [value]
        return f"(kb_fields -> '{name}') ?| {p(values)}::text[]"
    expr = f"(kb_fields ->> '{name}'){_JSON_CAST[kind]}"
    if isinstance(value, list):
        return f"{expr} = ANY({p(value)})"
    return f"{expr} = {p(value)}"


def compile_pgvector(checked: CheckedFilter, collection_id: str) -> Dict[str, Any]:
    """The filter for a pgvector table whose payload columns are ``kb_*`` and ``kb_f_<name>``."""
    return _postgres(checked, collection_id, _pg_column)


def compile_postgres_fts(checked: CheckedFilter, collection_id: str) -> Dict[str, Any]:
    """The filter for a :class:`~operonx_kb.stores.lexical.postgres.PostgresLexicalIndex` table."""
    return _postgres(checked, collection_id, _pg_json)


# ── SQLite FTS5 ──────────────────────────────────────────────────────────────


def _in(values: List[Any], params: List[Any]) -> str:
    params.extend(values)
    return "(" + ", ".join("?" * len(values)) + ")"


def _json_scalar(kind: str, value: Any) -> Any:
    return int(value) if kind == "bool" else value


def compile_sqlite_fts(checked: CheckedFilter, collection_id: str) -> Tuple[str, List[Any]]:
    """The filter for a :class:`~operonx_kb.stores.lexical.sqlite.SqliteLexicalIndex` table."""
    f, params = checked.filter, [collection_id]
    where = ["kb_collection = ?"]
    if f.document_ids is not None:
        where.append(f"kb_document IN {_in(list(f.document_ids), params)}")
    if f.tags_any is not None:
        where.append(
            f"EXISTS (SELECT 1 FROM json_each(kb_tags) WHERE value IN {_in(list(f.tags_any), params)})"
        )
    for tag in f.tags_all or []:
        where.append("EXISTS (SELECT 1 FROM json_each(kb_tags) WHERE value = ?)")
        params.append(tag)
    if f.acl_any is not None:
        where.append(
            f"EXISTS (SELECT 1 FROM json_each(kb_acl) WHERE value IN {_in(list(f.acl_any), params)})"
        )
    if f.mime_in is not None:
        where.append(f"kb_mime IN {_in(list(f.mime_in), params)}")
    if checked.created_after is not None:
        where.append("kb_created >= ?")
        params.append(checked.created_after)
    if checked.created_before is not None:
        where.append("kb_created < ?")
        params.append(checked.created_before)
    for name, value in checked.fields.items():
        kind = checked.types[name]
        values = [_json_scalar(kind, v) for v in (value if isinstance(value, list) else [value])]
        path = f"'$.\"{name}\"'"
        if kind == "keyword[]":
            where.append(
                f"EXISTS (SELECT 1 FROM json_each(kb_fields, {path}) WHERE value IN {_in(values, params)})"
            )
        else:
            where.append(f"json_extract(kb_fields, {path}) IN {_in(values, params)}")
    return " AND ".join(where), params


# ── Qdrant ───────────────────────────────────────────────────────────────────


def _match(key: str, values: List[Any]) -> Dict[str, Any]:
    if len(values) == 1:
        return {"key": key, "match": {"value": values[0]}}
    return {"key": key, "match": {"any": values}}


def compile_qdrant(checked: CheckedFilter, collection_id: str) -> Dict[str, Any]:
    """A Qdrant condition tree. A payload list matches when any of its values does,
    which is ``tags_any``; ``tags_all`` is one condition per tag. Floats and
    times are ranges, since Qdrant matches only keywords, integers and booleans."""
    f = checked.filter
    must: List[Dict[str, Any]] = [{"key": "kb_collection", "match": {"value": collection_id}}]
    if f.document_ids is not None:
        must.append(_match("kb_document", list(f.document_ids)))
    if f.tags_any is not None:
        must.append(_match("kb_tags", list(f.tags_any)))
    for tag in f.tags_all or []:
        must.append({"key": "kb_tags", "match": {"value": tag}})
    if f.acl_any is not None:
        must.append(_match("kb_acl", list(f.acl_any)))
    if f.mime_in is not None:
        must.append(_match("kb_mime", list(f.mime_in)))
    created: Dict[str, float] = {}
    if checked.created_after is not None:
        created["gte"] = checked.created_after
    if checked.created_before is not None:
        created["lt"] = checked.created_before
    if created:
        must.append({"key": "kb_created", "range": created})
    for name, value in checked.fields.items():
        key, kind = field_key(name), checked.types[name]
        values = value if isinstance(value, list) else [value]
        if kind in ("float", "datetime"):
            must.append({"should": [{"key": key, "range": {"gte": v, "lte": v}} for v in values]})
        else:
            must.append(_match(key, values))
    return {"must": must}


# ── dispatch ─────────────────────────────────────────────────────────────────


def _backend(obj: Any) -> str:
    from operonx_kb.stores.lexical.base import LexicalIndex

    if isinstance(obj, LexicalIndex):
        return f"{obj.dialect}_fts"
    # Vector stores by class: the operonx backends import their client lazily,
    # so naming the class here needs none of the optional extras.
    for cls in type(obj).__mro__:
        name = f"{cls.__module__}.{cls.__qualname__}"
        if name == "operonx.providers.vector_stores.faiss.FaissVectorStore":
            return "faiss"
        if name == "operonx.providers.vector_stores.pgvector.PgVectorStore":
            return "pgvector"
        if name == "operonx.providers.vector_stores.qdrant.QdrantVectorStore":
            return "qdrant"
    return type(obj).__name__


_COMPILERS = {
    "pgvector": compile_pgvector,
    "qdrant": compile_qdrant,
    "sqlite_fts": compile_sqlite_fts,
    "postgres_fts": compile_postgres_fts,
}


def native_filter(backend: Any, checked: CheckedFilter, collection_id: str) -> Any:
    """The native filter of ``backend`` (a vector store or a lexical index), or a
    :class:`PostFilter` for FAISS.

    Raises:
        FilterError: No compiler exists for this backend.
    """
    kind = _backend(backend)
    if kind == "faiss":
        return PostFilter("FAISS stores no payload")
    if kind not in _COMPILERS:
        raise FilterError(
            f"no KBFilter compiler for {kind}; the KB filters on FAISS (post-filter), pgvector, "
            "Qdrant, SQLite FTS5 and Postgres FTS. Searching it unfiltered could return other "
            "collections' documents, so the search is refused."
        )
    return _COMPILERS[kind](checked, collection_id)


def describe(native: Any) -> Optional[str]:
    """A short JSON rendering of a native filter, for traces."""
    if native is None or isinstance(native, PostFilter):
        return None
    return json.dumps(native, ensure_ascii=False, default=str)[:500]
