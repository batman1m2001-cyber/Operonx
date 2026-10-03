"""Hashes, content-derived ids and component fingerprints (track5 §6).

Everything is SHA-256 hex. Ids are deterministic functions of their inputs,
so re-running an ingest produces the same ids and the writes are idempotent by
construction (the idea Unstructured uses for its element ids).

- An id is a typed prefix and 32 hex characters (128 bits), so ``doc_…``,
  ``ver_…``, ``el_…`` and ``ch_…`` are readable in traces and in Studio.
- Parts are joined with a unit separator (U+001F) before hashing, so
  ``("ab", "c")`` and ``("a", "bc")`` never collide.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from operonx_kb.text.normalize import normalize_inline

__all__ = [
    "sha256_bytes",
    "sha256_text",
    "text_sha",
    "make_id",
    "document_id",
    "version_id",
    "element_id",
    "chunk_id",
    "canonical_json",
    "fingerprint",
    "combine_fingerprints",
]

_SEP = "\x1f"
_ID_HEX = 32


def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex of raw bytes: the blob key of a source file."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """SHA-256 hex of ``text`` encoded as UTF-8, exactly as given (no normalisation).

    Use for strings that are already canonical, such as a version's canonical
    text, whose hash is also its blob key.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def text_sha(text: str) -> str:
    """SHA-256 hex of ``text`` after :func:`normalize_inline`.

    Two texts that differ only in Unicode composition or whitespace hash the same.
    """
    return sha256_text(normalize_inline(text))


def _digest(*parts: Any) -> str:
    joined = _SEP.join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def make_id(prefix: str, *parts: Any) -> str:
    """``f"{prefix}_{first 32 hex of H(parts)}"``."""
    return f"{prefix}_{_digest(*parts)[:_ID_HEX]}"


def document_id(collection_id: str, key: str) -> str:
    """Stable identity of a logical document across its versions."""
    return make_id("doc", collection_id, key)


def version_id(document_id: str, raw_sha: str, pipeline_fp: str) -> str:
    """One version per (source bytes, pipeline): re-ingesting the same bytes with
    the same pipeline lands on the same version."""
    return make_id("ver", document_id, raw_sha, pipeline_fp)


def element_id(version_id: str, path: str) -> str:
    """Positional, per version. ``path`` is the dotted ordinal path from the root."""
    return make_id("el", version_id, path)


def chunk_id(document_id: str, chunker_fp: str, content_sha: str, occurrence: int) -> str:
    """Stable across versions: an unchanged chunk keeps its id, its embedding and
    its index entries. ``occurrence`` tells identical chunks of one document apart."""
    return make_id("ch", document_id, chunker_fp, content_sha, occurrence)


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, non-ASCII kept."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def fingerprint(component: str, version: str, config: Mapping[str, Any] | None = None) -> str:
    """``H(component, version, config)`` — what a transformation's output depends on.

    Every parser, layout model, structurer, serializer, chunker and embedder has
    one. A change in any of them is a visible new version or index generation,
    never silent drift (track5 §6.3, principle 4).

    Args:
        component: Qualified name of the component, e.g. ``"operonx_kb.parsing.MarkdownParser"``.
        version: The component's own version string; bump it when its output changes.
        config: Every setting that changes the output.
    """
    return _digest(component, version, canonical_json(dict(config or {})))[:_ID_HEX]


def combine_fingerprints(**parts: str) -> str:
    """One fingerprint from several named ones, order independent."""
    return _digest(canonical_json(parts))[:_ID_HEX]
