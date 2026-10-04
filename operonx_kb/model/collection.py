"""Collections and their spec: data, not code (track5 §7.9).

A spec names *resources* (``embedding:…``, ``vector_store:…``) by key and *pure
algorithms* (chunker, layout) by kind and parameters. It is stored in the
catalog and is round-trippable to YAML.
"""

from __future__ import annotations

import re
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "AnalyzerSpec",
    "ChunkerSpec",
    "LayoutSpec",
    "DenseIndexSpec",
    "LexicalIndexSpec",
    "FieldType",
    "CollectionSpec",
    "Collection",
]

#: The type of a field a collection declares filterable (track5 §12.3).
FieldType = Literal["keyword", "keyword[]", "int", "float", "datetime", "bool"]

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChunkerSpec(_Spec):
    """Which chunker, with which budget.

    Attributes:
        kind: ``"structural"`` (default; heading-scoped, evidence units for tables
            and figures) or ``"recursive"`` (separator-based baseline).
        max_tokens: Budget per chunk, counted by the chunker's tokenizer.
        min_tokens: Structural only: a section remainder below this is merged into
            its neighbour under the same heading path.
        heading_context: Prefix the embedded text with the heading path.
    """

    kind: Literal["structural", "recursive"] = "structural"
    max_tokens: int = Field(default=400, ge=16)
    min_tokens: int = Field(default=32, ge=0)
    heading_context: bool = True


class LayoutSpec(_Spec):
    """How paginated sources are laid out.

    Attributes:
        model: ``"heuristic"`` (default, no ML) or ``"docling"`` (the ``layout``
            extra; not available before phase K1b).
    """

    model: Literal["heuristic"] = "heuristic"


class DenseIndexSpec(_Spec):
    """A dense (vector) index derived from the collection's chunks.

    The index is an operonx vector store (``vector_store:`` resource: FAISS,
    pgvector, Qdrant), written with ``VectorUpsertOp`` and ``VectorDeleteOp``.
    It holds chunk vectors under int64 keys (:func:`operonx_kb.model.ids.vector_id`)
    and nothing else; the catalog records every key written
    (``kb_index_entries``), so garbage collection and ``verify`` never need to
    enumerate the index.

    Attributes:
        embedder: Resource key of the embedder. A bare name means
            ``embedding:<name>``; a key with ``:`` is used as is.
        store: Resource key of the vector store. A bare name means
            ``vector_store:<name>``.
        collection: The vector store's collection (FAISS collection, pgvector
            table, Qdrant collection); ``None`` uses the resource's default.
        batch_size: Texts per embedder call.
        passage_template: How a chunk's text is presented to the embedder;
            ``{text}`` is the chunk's embed text. E5 models want
            ``"passage: {text}"``. Part of the embedding cache key.
        query_template: The same for a query: ``"query: {text}"`` for E5.
    """

    embedder: str
    store: str
    collection: Optional[str] = None
    batch_size: int = Field(default=64, ge=1)
    passage_template: str = "{text}"
    query_template: str = "{text}"

    @field_validator("passage_template", "query_template")
    @classmethod
    def _has_text(cls, value: str) -> str:
        if "{text}" not in value:
            raise ValueError(f"template {value!r} must contain {{text}}")
        return value


class AnalyzerSpec(_Spec):
    """How text becomes lexical tokens (PLAN R2).

    Attributes:
        kind: ``"simple"`` (NFC, casefold, words of letters and digits) or
            ``"vi"`` (``simple`` plus the bigrams of adjacent syllables in a
            phrase: Vietnamese words are mostly two space-separated syllables).
        fold_diacritics: Strip tone and vowel marks and map ``đ`` to ``d``,
            so an unaccented query finds accented text (and words that differ
            only by their marks become one).
    """

    kind: Literal["simple", "vi"] = "simple"
    fold_diacritics: bool = False


class LexicalIndexSpec(_Spec):
    """A lexical (BM25-style) index derived from the collection's chunks.

    The index is a ``kb_lexical:`` resource (SQLite FTS5 or Postgres FTS)
    holding analyzed chunk text and the filter payload under the same int64
    keys as the dense index, recorded in the same ledger.

    Attributes:
        index: Resource key of the lexical index. A bare name means
            ``kb_lexical:<name>``.
        collection: Table inside the index (a SQL identifier); ``None`` uses
            ``"default"``.
        analyzer: How chunk and query text are tokenised.
    """

    index: str = "kb_lexical:main"
    collection: Optional[str] = None
    analyzer: AnalyzerSpec = AnalyzerSpec()

    @field_validator("collection")
    @classmethod
    def _identifier(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not _IDENT.match(value):
            raise ValueError(
                f"lexical collection {value!r} must be a lowercase SQL identifier "
                "(letters, digits, '_', at most 41 characters)"
            )
        return value


class CollectionSpec(_Spec):
    """Everything that decides how a collection's documents are processed.

    Attributes:
        filterable: Document metadata fields a ``KBFilter`` may name, with
            their types. Their values are copied from a document's
            ``metadata`` into every index entry (``kb_f_<name>``); a filter
            on an undeclared field raises.
    """

    chunker: ChunkerSpec = ChunkerSpec()
    layout: LayoutSpec = LayoutSpec()
    dense: Optional[DenseIndexSpec] = None
    lexical: Optional[LexicalIndexSpec] = None
    filterable: Dict[str, FieldType] = Field(default_factory=dict)
    language: Optional[str] = None

    @field_validator("filterable")
    @classmethod
    def _field_names(cls, value: Dict[str, str]) -> Dict[str, str]:
        bad = sorted(k for k in value if not _IDENT.match(k))
        if bad:
            raise ValueError(
                f"filterable field names {bad} must be lowercase identifiers: they become "
                "index columns (kb_f_<name>)"
            )
        return value


class Collection(BaseModel):
    """A named set of documents sharing one spec."""

    model_config = ConfigDict(extra="forbid")

    id: str
    spec: CollectionSpec = CollectionSpec()
    tags: List[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_\-]{0,63}", value):
            raise ValueError(
                f"collection id {value!r} must be a slug: lowercase letters, digits, '_' or '-', "
                "starting with a letter or digit, at most 64 characters"
            )
        return value
