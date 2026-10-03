"""Collections and their spec: data, not code (track5 §7.9).

A spec names *resources* (``embedding:…``, ``kb_index:…``) by key and *pure
algorithms* (chunker, layout) by kind and parameters. It is stored in the
catalog and is round-trippable to YAML.
"""

from __future__ import annotations

import re
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["ChunkerSpec", "LayoutSpec", "DenseIndexSpec", "CollectionSpec", "Collection"]


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

    Attributes:
        name: Unique within the collection.
        embedder: Resource key of the embedder. A bare name means
            ``embedding:<name>``; a key with ``:`` is used as is.
        index: Resource key of the index store (``kb_index:<name>``).
        batch_size: Texts per embedder call.
    """

    name: str = "dense"
    embedder: str
    index: str
    batch_size: int = Field(default=64, ge=1)


class CollectionSpec(_Spec):
    """Everything that decides how a collection's documents are processed."""

    chunker: ChunkerSpec = ChunkerSpec()
    layout: LayoutSpec = LayoutSpec()
    dense: Optional[DenseIndexSpec] = None
    language: Optional[str] = None


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
