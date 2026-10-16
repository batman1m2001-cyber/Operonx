"""The memory tools an agent calls: ``recall``, ``remember``, ``forget``.

The owner is never an argument: ``owner(ctx)`` reads it from the run (its
``deps``, typically the signed-in person), so a model cannot ask for
someone else's items. ``check(text)`` is the application's rule on what may
be kept (no amounts, no account numbers…): it returns why not, or ``None``.
``remember`` and ``forget`` read their effect back (``verify``); with
``confirm=True`` they wait for the person's approval first.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

from operonx_agents.memory.store import KINDS, MemoryStore, search
from operonx_agents.run.context import RunContext
from operonx_agents.tools.tool import Tool, tool

__all__ = ["memory_tools"]


def memory_tools(
    store: MemoryStore,
    *,
    owner: Callable[[RunContext], str],
    kinds: Iterable[str] = KINDS,
    max_chars: int = 200,
    check: Optional[Callable[[str], Optional[str]]] = None,
    expires: Optional[Callable[[str], Optional[float]]] = None,
    confirm: bool = False,
    limit: int = 15,
) -> List[Tool]:
    """``[recall, remember, forget]`` over ``store`` for the run's owner.

    Args:
        owner: The owner of the run, from its context.
        kinds: The kinds an item may have.
        max_chars: The longest text kept.
        check: Why a text may not be kept, or ``None``.
        expires: When an item of a kind expires (a time), or ``None`` for never.
        confirm: Writes wait for the person's approval.
        limit: The most items ``recall`` returns.
    """
    kinds = tuple(kinds)
    approval = "always" if confirm else "never"

    @tool(readonly=True)
    async def recall(ctx: RunContext, query: str = "") -> List[Dict[str, Any]]:
        """What is remembered about the person: their active items (kind, text, id),
        those matching the query first; an empty query lists them."""
        got = await search(store, owner(ctx), query, limit=limit)
        return [{"id": i.id, "kind": i.kind, "text": i.text} for i in got]

    async def remembered(ctx: RunContext, args: Dict[str, Any], result: Any):
        item = (
            await store.get(owner(ctx), result.get("id", "")) if isinstance(result, dict) else None
        )
        return True if item is not None else "the item is not in the store"

    @tool(approval=approval, idempotent=False, verify=remembered)
    async def remember(ctx: RunContext, kind: str, text: str) -> Dict[str, Any]:
        """Keep one short thing about the person for later conversations, only when
        they ask to be remembered: kind is preference, goal, fact or constraint; text is one
        sentence."""
        if kind not in kinds:
            raise ValueError(f"kind must be one of {', '.join(kinds)}")
        text = " ".join(str(text or "").split())
        if len(text) > max_chars:
            raise ValueError(f"the text is {len(text)} characters; keep it under {max_chars}")
        why = check(text) if check else None
        if why:
            raise ValueError(f"not kept: {why}")
        item = await store.add(
            owner(ctx),
            kind,
            text,
            source={"run": ctx.run_id, "session": ctx.session_id},
            expires=expires(kind) if expires else None,
        )
        return {"id": item.id, "kind": item.kind, "text": item.text}

    async def forgotten(ctx: RunContext, args: Dict[str, Any], result: Any):
        return (
            True
            if await store.get(owner(ctx), args.get("item_id", "")) is None
            else "the item is still there"
        )

    @tool(
        approval=approval, idempotent=True, verify=forgotten
    )  # not ``destructive``: ``confirm`` decides
    async def forget(ctx: RunContext, item_id: str) -> str:
        """Delete one remembered item (its id from recall), when the person asks to forget it."""
        if not await store.delete(owner(ctx), item_id):
            raise ValueError("no such item")
        return "forgotten"

    return [recall, remember, forget]
