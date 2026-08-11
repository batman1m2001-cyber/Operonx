"""Agent memory — what the agent knows before the conversation starts.

A provider answers two questions:

- **prefetch** — given the turn's query, what context is worth adding?
- **write** — given something learned, persist it for later runs.

Deliberately not a vector store. `VectorSearchOp` and `DocFetchOp`
already do retrieval, and a memory provider that wrapped them would just
be a worse version of both. This is the *small, always-loaded* layer:
project conventions, prior decisions, the handful of facts an agent
should never have to search for.

Two rules the base class enforces so backends cannot get them wrong:

**Prefetch fails soft.** Memory is an enrichment. A provider that is
slow, misconfigured or down must degrade the answer, never fail the turn
— an agent that refuses to respond because a notes file is unreadable is
worse than one that answers without it. `prefetch()` therefore catches
and returns empty.

**Writes fail loud.** The opposite direction: a write that silently does
nothing produces an agent that appears to learn and does not. That is
discovered weeks later, if ever.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, List, Optional

from operonx.core.loggings import LOGGER

__all__ = ["MemoryEntry", "MemoryProvider", "LocalMarkdownMemory"]


class MemoryEntry:
    """One retrieved fragment, with enough provenance to cite it."""

    __slots__ = ("text", "source", "score")

    def __init__(self, text: str, source: str = "", score: float = 0.0) -> None:
        self.text = text
        self.source = source
        self.score = score

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"MemoryEntry(source={self.source!r}, score={self.score:.2f})"

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, MemoryEntry) and (self.text, self.source, self.score) == (
            other.text,
            other.source,
            other.score,
        )


class MemoryProvider(ABC):
    """Base for memory backends.

    Subclasses implement ``_prefetch`` and ``_write``; the public methods
    add the failure policy. ``bound`` mirrors the op hint so a caller can
    route a networked provider onto the I/O queue and a file-backed one
    onto the CPU queue without knowing which is which.
    """

    #: Execution hint for the op that calls this provider.
    bound: str = "io"

    #: Shown to the model above this provider's fragments.
    label: str = "memory"

    @abstractmethod
    async def _prefetch(self, query: str, limit: int) -> List[MemoryEntry]:
        """Return at most ``limit`` entries relevant to ``query``."""

    @abstractmethod
    async def _write(self, text: str, source: str) -> None:
        """Persist ``text``. Raise on failure."""

    async def prefetch(self, query: str, limit: int = 5) -> List[MemoryEntry]:
        """Entries relevant to ``query``, or ``[]`` if anything went wrong.

        Never raises. The error is logged once per failure with the
        provider named, so a permanently broken provider is visible in
        the logs rather than showing up as an agent that has quietly
        stopped remembering anything.
        """
        if not query or limit <= 0:
            return []
        try:
            entries = await self._prefetch(query, limit)
        except Exception as exc:  # noqa: BLE001 - enrichment must not fail the turn
            LOGGER.error(
                "memory: %s.prefetch failed, continuing without it: %s: %s",
                type(self).__name__,
                type(exc).__name__,
                exc,
            )
            return []
        return [e for e in entries if isinstance(e, MemoryEntry) and e.text][:limit]

    async def write(self, text: str, source: str = "agent") -> None:
        """Persist ``text``. Raises — see the module docstring."""
        if not text or not text.strip():
            raise ValueError("refusing to write empty memory — nothing to recall later")
        await self._write(text, source)


class LocalMarkdownMemory(MemoryProvider):
    """A markdown file, one fact per bullet.

    The point is that a human can read and edit it. Retrieval is keyword
    overlap rather than embeddings, which is honest about what this is:
    a notes file the agent can consult, not a search index. When the file
    grows past what keyword matching serves, that is the signal to put a
    real vector store behind `VectorSearchOp` instead of making this
    cleverer.

    Args:
        path: The markdown file. Created on first write; a missing file
            reads as empty rather than raising, so a fresh project works
            with no setup.
        label: Heading shown above these fragments in the prompt.
    """

    bound = "cpu"

    #: Bullets, optionally nested. Anything else in the file (headings,
    #: prose) is context for the human and is not retrieved.
    _BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
    _WORD = re.compile(r"[a-z0-9]+")

    def __init__(self, path: str | Path, label: str = "project memory") -> None:
        self.path = Path(path)
        self.label = label

    def _entries(self) -> List[str]:
        if not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [m.group(1).strip() for line in lines if (m := self._BULLET.match(line))]

    @classmethod
    def _tokens(cls, text: str) -> set[str]:
        # Single characters are noise ("a", "I") and match everything.
        return {w for w in cls._WORD.findall(text.lower()) if len(w) > 1}

    async def _prefetch(self, query: str, limit: int) -> List[MemoryEntry]:
        wanted = self._tokens(query)
        if not wanted:
            return []
        scored = []
        for text in self._entries():
            tokens = self._tokens(text)
            if not tokens:
                continue
            overlap = len(wanted & tokens)
            if overlap:
                # Normalise by the entry's own length so a long bullet
                # does not outrank a precise short one just by covering
                # more ground.
                scored.append(MemoryEntry(text, str(self.path), overlap / len(tokens)))
        scored.sort(key=lambda e: e.score, reverse=True)
        return scored[:limit]

    async def _write(self, text: str, source: str) -> None:
        fact = text.strip()

        # Re-learning the same fact must not grow the file forever — an
        # agent that writes memory every turn would otherwise degrade its
        # own retrieval one duplicate at a time.
        if fact in self._entries():
            return

        existing = self.path.read_text(encoding="utf-8") if self.path.is_file() else ""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            if not existing:
                fh.write(f"# {self.label}\n\n")
            elif not existing.endswith("\n"):
                # A hand-edited file may not end in a newline; appending
                # blind would splice the new bullet onto the last line.
                fh.write("\n")
            fh.write(f"- {fact}\n")


def render_memory_block(entries: List[MemoryEntry], label: str = "memory") -> Optional[str]:
    """Format entries as one prompt block, or ``None`` if there are none.

    Returning ``None`` rather than an empty block matters for prompt
    caching: an always-present ``<memory></memory>`` wrapper that is
    sometimes empty still changes the prefix, and a changed prefix is a
    cache miss on every turn.
    """
    if not entries:
        return None
    body = "\n".join(f"- {e.text}" for e in entries)
    return f"<{label}>\n{body}\n</{label}>"
