"""Requests, their keys, and enricher fingerprints (PLAN E1).

A request is what one model call is asked: ``{"key", "messages"}``. Its key is
the SHA-256 of the messages, so any change in what the model would read —
the text, the prompt, the order — is a different key, and an unchanged input
is answered from the catalog's cache. What the messages do not show (the
model, its answer limit, the parser) is in the enricher fingerprint, the
cache's other key.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping

from operonx_kb.model.ids import canonical_json, fingerprint, sha256_text
from operonx_kb.text.tokenize import Tokenizer

__all__ = ["make_request", "request_key", "enricher_fingerprint", "truncate"]

_WORD = re.compile(r"\S+")


def request_key(messages: List[Dict[str, Any]]) -> str:
    """The cache key of a request: the hash of the messages a model reads."""
    return sha256_text(canonical_json(messages))


def make_request(system: str, user: str) -> Dict[str, Any]:
    """A request of a system and a user message, with its key."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return {"key": request_key(messages), "messages": messages}


def enricher_fingerprint(
    kind: str, version: str, llm: str, settings: Mapping[str, Any] | None = None
) -> str:
    """``H(kind, version, model, settings)`` of an enrichment stage.

    Args:
        kind: The stage (``"contextual"``, ``"summary"``, ``"toc"``).
        version: Its prompt's version; bump it when the prompt changes.
        llm: The model's fingerprint (``ops._resources.llm_fingerprint``).
        settings: What else changes an answer and is not in the messages.
    """
    return fingerprint(f"operonx_kb.enrich.{kind}", version, {"llm": llm, **dict(settings or {})})


def truncate(text: str, max_tokens: int, tokenizer: Tokenizer) -> str:
    """``text`` cut at a word boundary to at most ``max_tokens``, with ``…`` when cut."""
    if tokenizer.count(text) <= max_tokens:
        return text
    count, end = 0, 0
    for match in _WORD.finditer(text):
        n = tokenizer.count(match.group())
        if count + n > max_tokens:
            break
        count += n
        end = match.end()
    return text[:end].rstrip() + " …"
