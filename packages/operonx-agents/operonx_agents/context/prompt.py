"""Prompt assembly, shaped around what prompt caching charges for.

Ported from ``operonx.agents.ops.prompt_ops``. Providers cache a prefix,
so everything that changes per turn goes after everything that does not:

    system prompt       byte-stable for the run
    conversation        grows at the end
    notices             per turn (the budget notice), last

Cache breakpoints (Anthropic's explicit ones; OpenAI-compatible backends
drop the marker) go on the system prompt **and** on the last committed
message, so the conversation so far is cached too, not only the
instructions (``operonx.agents`` marked the system prompt alone).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["CACHE_MARKER", "assemble", "prefix_is_stable"]

#: The backend-neutral breakpoint marker (``lift_cache_control`` moves it
#: where Anthropic reads it).
CACHE_MARKER = {"type": "ephemeral"}


def assemble(
    system: str,
    messages: List[Dict[str, Any]],
    notices: Optional[List[Dict[str, Any]]] = None,
    *,
    cache: bool = True,
) -> List[Dict[str, Any]]:
    """The request's messages: system, conversation, notices. With
    ``cache``, breakpoints on the system prompt and the last conversation
    message. The inputs are not mutated: a marker written into the
    conversation would persist into the next turn's history."""
    out: List[Dict[str, Any]] = []
    if system and system.strip():
        head = {"role": "system", "content": system}
        out.append(_marked(head) if cache else head)
    if messages:
        out.extend(messages[:-1])
        last = messages[-1]
        out.append(_marked(last) if cache and last.get("content") else last)
    out.extend(notices or ())
    return out


def _marked(message: Dict[str, Any]) -> Dict[str, Any]:
    return {**message, "cache_control": CACHE_MARKER}


def prefix_is_stable(
    previous: Optional[List[Dict[str, Any]]], current: Optional[List[Dict[str, Any]]]
) -> Dict[str, Any]:
    """Compare two requests' messages and report where the prefix diverged.

    A cache miss is invisible at runtime — the answer is the same, only
    slower and dearer — so this makes it testable. Breakpoint markers are
    ignored: they move to the last message every turn by design, and are
    not part of the bytes cached. ``shared`` counts the leading messages
    that matched; ``stable`` means all of ``previous`` did.
    """
    previous = [_bare(m) for m in previous or () if isinstance(m, dict)]
    current = [_bare(m) for m in current or () if isinstance(m, dict)]
    shared = 0
    for old, new in zip(previous, current):
        if old != new:
            break
        shared += 1
    diverged: Optional[str] = None
    if shared < min(len(previous), len(current)):
        old, new = previous[shared], current[shared]
        if old.get("role") != new.get("role"):
            diverged = f"role changed at index {shared}: {old.get('role')} → {new.get('role')}"
        else:
            diverged = f"content changed at index {shared} (role={new.get('role')})"
    return {
        "shared": shared,
        "stable": shared >= len(previous) if previous else True,
        "diverged": diverged,
    }


def _bare(message: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in message.items() if k != "cache_control"}
