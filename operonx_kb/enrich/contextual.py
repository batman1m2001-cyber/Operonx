"""Contextual chunk enrichment at section scope (track5 §11.4, PLAN E2-E3).

Anthropic's contextual retrieval: a model writes a sentence or two that
situate a chunk in its document, and that context is embedded and indexed
with the chunk. Here the model sees the chunk's **section window**, not the
whole document: the chunks of one section are packed in order into windows of
at most ``window_tokens``, and a chunk is shown the window that holds it. An
edit then changes the input of one window's chunks only, and a long document
is never sent whole once per chunk.

The window comes before the chunk in the prompt, so the chunks of one window
share a prefix a provider's prompt cache can serve (OpenAI caches prefixes of
1024 tokens or more at half the input price).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from operonx_kb.chunking.base import ChunkDraft
from operonx_kb.enrich.base import make_request
from operonx_kb.model.document import Span
from operonx_kb.structure.build import VersionTree
from operonx_kb.text.spans import chunk_text
from operonx_kb.text.tokenize import Tokenizer

__all__ = ["PROMPT_VERSION", "CONTEXT_SYSTEM", "windows", "context_request", "with_context"]

#: Bump when :data:`CONTEXT_SYSTEM` or the user message's layout changes.
PROMPT_VERSION = "1"

CONTEXT_SYSTEM = (
    "You write the context of a chunk of a document, to improve search retrieval of the chunk.\n"
    "You are given the document's title, the section the chunk belongs to, the part of that "
    "section around the chunk, and the chunk.\n"
    "Reply with the context only: one or two sentences, in the language of the chunk, that "
    "situate the chunk within the document — what it is about and which part of the document "
    "it belongs to, naming the people, places, products, terms and dates it refers to but "
    "leaves implicit. Do not repeat the chunk, do not answer questions, add no preamble."
)


def _sections(tree: VersionTree) -> List[Span]:
    """Spans of the version's sections, the whole text first (the root section)."""
    out: List[Span] = [(0, len(tree.canonical))]
    out += [e.span for e in tree.elements if e.kind == "section" and e.span is not None]
    return out


def _section_index(sections: Sequence[Span], offset: int) -> int:
    """The innermost section holding ``offset``: the shortest span that does."""
    holding = [i for i, (start, end) in enumerate(sections) if start <= offset < end]
    return min(holding, key=lambda i: sections[i][1] - sections[i][0], default=0)


def windows(
    tree: VersionTree, drafts: Sequence[ChunkDraft], window_tokens: int, tokenizer: Tokenizer
) -> List[str]:
    """For each draft, the text of its section window.

    A section's drafts, in order, are packed into windows of at most
    ``window_tokens``; a draft larger than that is a window of its own. A
    window's text is its chunks' texts joined by a blank line.
    """
    sections = _sections(tree)
    texts = [chunk_text(tree.canonical, d.spans) for d in drafts]
    by_section: Dict[int, List[int]] = {}
    for i, d in enumerate(drafts):
        by_section.setdefault(_section_index(sections, d.spans[0][0]), []).append(i)
    out: List[Optional[str]] = [None] * len(drafts)
    for members in by_section.values():
        groups: List[List[int]] = []
        used = 0
        for i in members:
            n = tokenizer.count(texts[i])
            if groups and used + n <= window_tokens:
                groups[-1].append(i)
                used += n
            else:
                groups.append([i])
                used = n
        for group in groups:
            text = "\n\n".join(texts[i] for i in group)
            for i in group:
                out[i] = text
    return [w or "" for w in out]


def context_request(
    title: Optional[str], heading_path: Sequence[str], window: str, chunk: str
) -> Dict[str, Any]:
    """The request for one chunk's context: ``{"key", "messages"}``."""
    section = " > ".join(heading_path) if heading_path else "(the document's opening)"
    user = (
        f"Document: {title or '(untitled)'}\n"
        f"Section: {section}\n\n"
        f"<section>\n{window}\n</section>\n\n"
        f"The chunk to situate:\n<chunk>\n{chunk}\n</chunk>"
    )
    return make_request(CONTEXT_SYSTEM, user)


def with_context(context: str, embed_text: str) -> str:
    """What is embedded and indexed for a contextualized chunk: the context, then
    the chunk's own embed text (its heading path and text)."""
    return f"{context.strip()}\n\n{embed_text}"
