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
    "OcrSpec",
    "DenseIndexSpec",
    "LexicalIndexSpec",
    "ContextualSpec",
    "TreeSpec",
    "GraphSpec",
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


class OcrSpec(_Spec):
    """OCR for scanned PDF pages (:mod:`operonx_kb.pdf.ocr`): a page with no text layer
    is rendered and read by Tesseract (the ``tesseract`` binary and its language data,
    e.g. ``apt install tesseract-ocr tesseract-ocr-vie``; rendering needs the ``ocr``
    extra). Pages with a text layer are never OCR'd.

    Attributes:
        languages: Tesseract language codes joined by ``+``.
        dpi: Resolution a page is rendered at.
        min_words: A page with fewer words than this is OCR'd.
        min_confidence: Words read below this confidence (0–100) are dropped.
    """

    engine: Literal["tesseract"] = "tesseract"
    languages: str = "vie+eng"
    dpi: int = Field(default=200, ge=72, le=600)
    min_words: int = Field(default=3, ge=1)
    min_confidence: float = Field(default=30.0, ge=0, le=100)


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


def _llm_name(value: str) -> str:
    """An ``llm:`` resource as ``LLMOp`` names it: ``"gpt-4o-mini"``, not ``"llm:gpt-4o-mini"``."""
    category, _, name = value.rpartition(":")
    if category not in ("", "llm") or not name:
        raise ValueError(
            f"{value!r} is not an llm: resource; name it as LLMOp does (e.g. 'gpt-4o-mini' "
            "for llm:gpt-4o-mini). A fake or another category is reached through "
            "ResourceHub.alias('llm:<name>', '<key>')"
        )
    return name


class ContextualSpec(_Spec):
    """Contextual chunk enrichment (PLAN E2): a model writes a sentence or two
    situating each chunk in its section, prepended to what is embedded and indexed.

    Attributes:
        llm: The ``llm:`` resource, by the name ``LLMOp`` takes (``"gpt-4o-mini"``).
        window_tokens: The most section text shown with a chunk: a section is cut
            into windows of whole chunks up to this many tokens, and a chunk sees
            the window that holds it.
        max_tokens: The model's answer limit.
        parallel: No effect since 0.2.1 (stages run 8 calls at once; limit a model with
            its resource's ``rate_limit:``). Kept so stored specs load.
    """

    llm: str
    window_tokens: int = Field(default=1500, ge=64)
    max_tokens: int = Field(default=160, ge=16)
    parallel: int = Field(default=8, ge=1)

    @field_validator("llm")
    @classmethod
    def _name(cls, value: str) -> str:
        return _llm_name(value)


class TreeSpec(_Spec):
    """The tree index and tree search (PLAN E5-E7).

    Attributes:
        llm: The ``llm:`` resource that writes summaries and tables of contents.
        navigator: The ``llm:`` resource that walks the tree at query time
            (default: ``llm``).
        summary_input_tokens: A node's whole text is summarized when it fits;
            a longer node is summarized from its opening and its children's titles.
        toc_min_tokens: A document without headings gets a synthesized table of
            contents from this length on.
        toc_window_tokens: The most text one table-of-contents call reads.
        max_tokens: The answer limit of a summary.
        parallel: No effect since 0.2.1 (stages run 8 calls at once; limit a model with
            its resource's ``rate_limit:``). Kept so stored specs load.
        docs: Documents tree search walks: the first ``docs`` distinct documents
            among the seed retriever's hits.
        seed_depth: Hits the seed retriever returns.
        beam: Nodes the navigator may choose per step.
        max_depth: Navigator steps per query, at most.
    """

    llm: str
    navigator: Optional[str] = None
    summary_input_tokens: int = Field(default=2000, ge=64)
    toc_min_tokens: int = Field(default=600, ge=1)
    toc_window_tokens: int = Field(default=6000, ge=256)
    max_tokens: int = Field(default=200, ge=16)
    parallel: int = Field(default=8, ge=1)
    docs: int = Field(default=5, ge=1)
    seed_depth: int = Field(default=30, ge=1)
    beam: int = Field(default=3, ge=1)
    max_depth: int = Field(default=4, ge=1)

    @field_validator("llm", "navigator")
    @classmethod
    def _names(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _llm_name(value)

    @property
    def navigator_llm(self) -> str:
        return self.navigator or self.llm


class GraphSpec(_Spec):
    """The concept graph and graph search (PLAN G1-G6).

    No model builds it: each chunk's concepts are the names its text holds
    (runs of capitalised words) plus its document title and headings, committed
    with the version. A search walks the chunk-concept graph from the seed
    retriever's best hits (personalized PageRank) and ranks chunks by where
    the walk ends.

    Attributes:
        title_weight: A heading's or title's weight on its chunk's edge, against
            1 per mention in the text: a chunk is most of all about its heading.
        seeds: The seed retriever's hits the walk restarts from (the first
            ``seeds``, weighted 1/rank).
        expand: Chunks the walk brings in beside the seeds, at most: the seeds and
            these come first (by the walk's mass), then the seed retriever's other
            hits in its order. More would push a single-hop answer the seed
            retriever ranked 4th-10th out of the top 10 (``docs/bench/k6.md``).
        alpha: The restart probability of the walk.
        max_df_share: A concept in more than this share of the collection's
            chunks (and in more than ``max_df_min`` chunks) is too common to
            link anything and is left out of the graph.
        max_df_min: The floor of that limit, for small collections.
        iterations: Power-iteration steps.
        seed_depth: Hits the seed retriever returns.
    """

    title_weight: float = Field(default=3.0, gt=0)
    seeds: int = Field(default=3, ge=1)
    expand: int = Field(default=5, ge=0)
    alpha: float = Field(default=0.3, gt=0, lt=1)
    max_df_share: float = Field(default=0.005, gt=0, le=1)
    max_df_min: int = Field(default=10, ge=1)
    iterations: int = Field(default=30, ge=1)
    seed_depth: int = Field(default=30, ge=1)


class CollectionSpec(_Spec):
    """Everything that decides how a collection's documents are processed.

    Attributes:
        filterable: Document metadata fields a ``KBFilter`` may name, with
            their types. Their values are copied from a document's
            ``metadata`` into every index entry (``kb_f_<name>``); a filter
            on an undeclared field raises.
        contextual: Contextual chunk enrichment; ``None`` (default) embeds
            chunks as they are.
        tree: The tree index (section summaries, tables of contents) and the
            ``tree`` retrieval mode; ``None`` (default) builds none.
        graph: The concept graph and the ``graph`` retrieval mode; ``None``
            (default) builds none.
        ocr: OCR for scanned PDF pages; ``None`` (default) leaves a page without
            a text layer empty.
    """

    chunker: ChunkerSpec = ChunkerSpec()
    layout: LayoutSpec = LayoutSpec()
    ocr: Optional[OcrSpec] = None
    dense: Optional[DenseIndexSpec] = None
    lexical: Optional[LexicalIndexSpec] = None
    filterable: Dict[str, FieldType] = Field(default_factory=dict)
    language: Optional[str] = None
    contextual: Optional[ContextualSpec] = None
    tree: Optional[TreeSpec] = None
    graph: Optional[GraphSpec] = None

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
