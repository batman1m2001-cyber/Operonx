"""Memory prefetch and write, as ops.

Prefetch fans out across providers: one generator frame per provider, a
bounded fetch per frame, an ordered gather at the end. That buys per-
provider concurrency, per-provider trace spans and per-provider ctx
isolation — and, more usefully here, it means one slow provider costs the
turn its own timeout rather than the sum of everyone's.

The deadline is enforced *inside* ``provider_prefetch`` rather than by the
scheduler. A per-item timeout would cancel the op; catching it here lets
the provider that timed out contribute nothing while its siblings still
contribute what they found — which is the whole point of memory being an
enrichment.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional

from operonx.agents.memory import MemoryEntry, MemoryProvider, render_memory_block
from operonx.core.loggings import LOGGER
from operonx.core.ops.transform.func_op import op

__all__ = ["each_provider", "provider_prefetch", "merge_memory", "memory_write"]


@op
def each_provider(providers: Optional[list] = None):
    """One frame per provider. No providers configured yields nothing, so
    an agent without memory pays no prefetch cost at all."""
    for index, provider in enumerate(providers or []):
        yield {"provider": provider, "index": index}


@op(bound="io")
async def provider_prefetch(
    provider: Any = None,
    query: str = "",
    limit: int = 5,
    deadline: float = 8.0,
) -> dict:
    """Fetch from one provider under a wall-clock deadline.

    Returns an empty list rather than raising on timeout or error.
    ``MemoryProvider.prefetch`` already swallows provider-side failures;
    this adds the deadline, which it cannot enforce on itself.
    """
    if provider is None or not query:
        return {"entries": [], "label": ""}

    label = getattr(provider, "label", "memory")
    try:
        entries = await asyncio.wait_for(provider.prefetch(query, limit), timeout=deadline)
    except asyncio.TimeoutError:
        LOGGER.warning(
            "memory: %s exceeded the %.1fs prefetch deadline; continuing without it",
            type(provider).__name__,
            deadline,
        )
        return {"entries": [], "label": label}
    except Exception as exc:  # noqa: BLE001 - a provider must never fail the turn
        LOGGER.error(
            "memory: %s.prefetch raised past its own guard: %s: %s",
            type(provider).__name__,
            type(exc).__name__,
            exc,
        )
        return {"entries": [], "label": label}
    return {"entries": list(entries or []), "label": label}


@op
def merge_memory(entries: Any = None, label: str = "memory") -> dict:
    """Fold gathered entries into one prompt block.

    ``collect()`` hands over a single frame's value when only one item
    was produced, and a list of them otherwise — inside a loop it can
    even arrive per item — so accept every shape rather than assuming
    the batched one.

    Yields ``context=None`` when nothing was found. An always-present but
    sometimes-empty ``<memory></memory>`` wrapper changes the prompt
    prefix on the turns it is empty, and a changed prefix is a
    prompt-cache miss on every one of them.
    """
    flat: List[MemoryEntry] = []
    for group in _iter_groups(entries):
        flat.extend(e for e in group if isinstance(e, MemoryEntry))

    # Same fact from two providers is one fact to the model.
    seen: set[str] = set()
    unique: List[MemoryEntry] = []
    for entry in sorted(flat, key=lambda e: e.score, reverse=True):
        key = entry.text.strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(entry)

    return {
        "context": render_memory_block(unique, label),
        "entries": unique,
        "count": len(unique),
    }


def _iter_groups(entries: Any):
    """Normalise collect()'s several shapes into an iterable of lists."""
    if entries is None:
        return
    if isinstance(entries, MemoryEntry):
        yield [entries]
        return
    if isinstance(entries, list):
        if entries and all(isinstance(e, MemoryEntry) for e in entries):
            yield entries
            return
        for item in entries:
            yield from _iter_groups(item)


@op(bound="io")
async def memory_write(
    provider: Any = None,
    text: str = "",
    source: str = "agent",
) -> dict:
    """Persist one fact.

    Unlike prefetch this reports failure. A write that silently does
    nothing produces an agent that appears to learn and does not, which
    is discovered weeks later if at all — so the outcome is returned for
    the caller to act on rather than logged and dropped.
    """
    if provider is None or not text or not text.strip():
        return {"written": False, "error": "nothing to write"}
    if not isinstance(provider, MemoryProvider):
        return {"written": False, "error": f"not a MemoryProvider: {type(provider).__name__}"}
    try:
        await provider.write(text, source)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        LOGGER.error("memory: write to %s failed: %s", type(provider).__name__, exc)
        return {"written": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"written": True, "error": ""}
