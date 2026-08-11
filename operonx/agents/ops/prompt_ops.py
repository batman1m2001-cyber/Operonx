"""Prompt assembly, shaped around what prompt caching actually charges for.

Providers cache a *prefix*. The cache hits only while the leading bytes
are identical to last turn, so anything that changes per turn must sit
**after** everything that does not. Get the order wrong and the cache
misses on every single turn — silently, since the only symptom is a
larger bill and slower first tokens.

Three rules encoded here:

**The system prompt must be byte-stable.** A timestamp, a turn counter or
a "you have used 3 of 25 turns" line invalidates the prefix every turn.
:func:`build_system_prompt` takes a date, not a datetime, for exactly
that reason — the date is stable for the whole session, and a session
that spans midnight is worth one cache miss a day.

**Per-turn context goes after the stable prefix, never inside it.**
Retrieved memory changes with the query, so injecting it into the system
prompt is the most expensive way to use memory. It travels as its own
message instead.

**Empty means absent.** An always-present ``<memory></memory>`` wrapper
that is sometimes empty still changes the prefix on the turns it is
empty. Blocks with nothing in them are omitted entirely.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Any, Dict, List, Optional

from operonx.core.ops.transform.func_op import op

__all__ = [
    "build_system_prompt",
    "assemble_api_messages",
    "apply_cache_control",
    "prefix_is_stable",
]


@op
def build_system_prompt(
    base: str = "",
    sections: Optional[dict] = None,
    today: Optional[str] = None,
) -> dict:
    """Assemble the system prompt from parts that do not change per turn.

    Args:
        base: The agent's standing instructions.
        sections: Extra named blocks — conventions, tool notes, anything
            constant for the session. Rendered in sorted order so two
            runs with the same content produce the same bytes; dict
            insertion order would otherwise make the cache depend on how
            the caller happened to build it.
        today: ISO date. Defaults to today. A **date**, not a timestamp:
            a clock in the prefix is a guaranteed cache miss every turn.

    Returns:
        ``prompt`` plus ``stable=True``, a marker downstream ops use to
        assert nothing per-turn crept in.
    """
    parts: List[str] = []
    if base and base.strip():
        parts.append(base.strip())

    for name in sorted(sections or {}):
        body = sections[name]
        if body and str(body).strip():
            parts.append(f"<{name}>\n{str(body).strip()}\n</{name}>")

    parts.append(f"Today's date is {today or _date.today().isoformat()}.")
    return {"prompt": "\n\n".join(parts), "stable": True}


@op
def assemble_api_messages(
    system: str = "",
    messages: Optional[list] = None,
    memory_context: Optional[str] = None,
    notices: Optional[list] = None,
) -> dict:
    """Build the final message list in cache-friendly order.

    Order is::

        system            stable, cached
        conversation      grows at the end, so the prefix survives
        memory_context    per-turn, therefore last
        notices           per-turn

    Memory deliberately trails the conversation rather than leading it.
    Retrieved context changes with every query, so placing it before the
    history would move the whole history out of the cached prefix — the
    most expensive possible position for it.
    """
    out: List[dict] = []
    if system and system.strip():
        out.append({"role": "system", "content": system})

    out.extend(m for m in (messages or []) if isinstance(m, dict))

    # Empty blocks are omitted, not rendered empty — see the module
    # docstring on why an empty wrapper still costs a cache miss.
    if memory_context and memory_context.strip():
        out.append({"role": "user", "content": memory_context})

    for notice in notices or []:
        if isinstance(notice, dict):
            out.append(notice)
        elif notice and str(notice).strip():
            out.append({"role": "user", "content": str(notice)})

    return {"messages": out, "count": len(out)}


@op
def apply_cache_control(
    messages: Optional[list] = None,
    breakpoints: int = 1,
    marker: Optional[dict] = None,
) -> dict:
    """Mark cache breakpoints on the stable prefix.

    Anthropic-style caching takes explicit breakpoints, capped at four.
    They belong on the **last** message of the stable prefix — marking a
    message that changes per turn caches something that is never reused,
    which costs a cache write and buys nothing.

    The stable prefix here is the leading system messages. Everything
    after grows or changes, so nothing later is worth a breakpoint.

    Args:
        breakpoints: How many to place, newest-first within the prefix.
            Providers cap this (four for Anthropic); more are ignored, so
            asking for more is silently wasted rather than an error.
        marker: What to attach. Defaults to Anthropic's ephemeral form.

    Returns:
        A new list — inputs are not mutated, because the caller's
        conversation cell is shared and a marker written into it would
        persist into next turn's history.
    """
    marker = marker or {"type": "ephemeral"}
    out = [dict(m) for m in (messages or []) if isinstance(m, dict)]

    prefix_end = 0
    for message in out:
        if message.get("role") != "system":
            break
        prefix_end += 1

    if prefix_end == 0 or breakpoints <= 0:
        return {"messages": out, "marked": 0}

    marked = 0
    for index in range(prefix_end - 1, -1, -1):
        if marked >= breakpoints:
            break
        out[index] = {**out[index], "cache_control": marker}
        marked += 1

    return {"messages": out, "marked": marked}


def prefix_is_stable(
    previous: Optional[List[dict]], current: Optional[List[dict]]
) -> Dict[str, Any]:
    """Compare two turns' messages and report where the prefix diverged.

    A cache miss is invisible at runtime — the response is identical, only
    slower and dearer — so this exists to make the failure testable and
    debuggable. Feed it consecutive turns; ``shared`` counts how many
    leading messages matched.
    """
    previous = [m for m in (previous or []) if isinstance(m, dict)]
    current = [m for m in (current or []) if isinstance(m, dict)]

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
