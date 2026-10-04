"""Compaction: older exchanges become a summary when the prompt fills up.

Ported from ``operonx.agents.ops.compact_ops`` (the exchange-safe
planner) with three changes:

- **It triggers on real usage**: the ``input_tokens`` the provider counted
  for the last request, against ``ContextPolicy.window``. Not the 3.5
  characters-per-token estimate, which is off by a large factor for
  Vietnamese.
- **The summary persists.** It is appended to the session as a
  ``summary`` item holding the summary and the messages kept verbatim
  after it, so the next run reads the compacted view back
  (:func:`view`) instead of summarising again, and the prompt prefix stays
  byte-stable until the next compaction.
- **Tool-result clearing first**: kept tool messages older than the last
  ``clear_tool_results_after`` get a stub instead of their content.

The invariant that matters most, kept from the port: compaction moves
whole exchanges. An assistant message that called tools is never
separated from the tool messages answering it, so a compacted history is
one a provider accepts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from operonx_agents.model.model import Model

__all__ = [
    "CLEARED",
    "ContextPolicy",
    "SUMMARY_MARKER",
    "clear_tool_results",
    "plan",
    "summary_item",
    "summary_prompt",
    "unmatched_tool_calls",
    "view",
]

#: Starts the message that stands in for the summarised span.
SUMMARY_MARKER = "[conversation summary]"

#: What a cleared tool result reads.
CLEARED = "[tool result cleared to save context; call the tool again if you need it]"


@dataclass(frozen=True)
class ContextPolicy:
    """When and how an agent's conversation is compacted.

    Attributes:
        window: The model's context window, in tokens.
        compact_at: Compact before the next call once a request's prompt
            reached this share of ``window``. Below 1.0 on purpose: the
            request that finds the window full has already failed.
        keep_recent: Exchanges kept verbatim. Recency is what the model is
            reasoning about; summarising it makes an agent forget what it
            just did.
        summarizer: The model that writes the summary. ``None`` drops the
            span with a note saying so.
        clear_tool_results_after: Keep the content of only this many of
            the most recent kept tool messages; ``None`` keeps all.
    """

    window: int
    compact_at: float = 0.75
    keep_recent: int = 6
    summarizer: Optional[Model] = None
    clear_tool_results_after: Optional[int] = None

    def __post_init__(self) -> None:
        if self.window <= 0:
            raise ValueError(f"ContextPolicy window must be positive tokens, got {self.window!r}.")
        if not 0 < self.compact_at <= 1:
            raise ValueError(
                f"ContextPolicy compact_at={self.compact_at!r} is a share of the window, in (0, 1]."
            )
        if self.keep_recent < 1:
            raise ValueError(
                "ContextPolicy keep_recent must be at least 1: the exchange the model is in the "
                "middle of is never summarised."
            )

    def due(self, input_tokens: int) -> bool:
        return input_tokens >= self.compact_at * self.window


def _exchanges(messages: List[dict]) -> List[List[dict]]:
    """Group messages into exchanges that must move together: an assistant
    message that called tools owns the tool messages answering it, found by
    call id wherever they arrive. A result whose call is nowhere is a group
    of its own."""
    groups: List[List[dict]] = []
    owner: Dict[str, int] = {}
    for message in messages:
        if message.get("role") == "tool":
            index = owner.get(message.get("tool_call_id"))
            if index is not None:
                groups[index].append(message)
            else:
                groups.append([message])
            continue
        groups.append([message])
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if isinstance(call, dict) and call.get("id"):
                    owner[call["id"]] = len(groups) - 1
    return groups


def _pairs(messages: List[dict]) -> Tuple[set, set]:
    calls = {
        c.get("id") for m in messages for c in (m.get("tool_calls") or []) if isinstance(c, dict)
    }
    results = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    return calls, results


def _paired(group: List[dict]) -> bool:
    calls, results = _pairs(group)
    calls.discard(None)
    results.discard(None)
    return calls == results


def unmatched_tool_calls(messages: Optional[List[dict]]) -> Dict[str, Any]:
    """Broken tool pairing, the failure compaction most risks: calls with
    no result, results with no call."""
    messages = [m for m in (messages or []) if isinstance(m, dict)]
    calls, results = _pairs(messages)
    return {
        "calls_without_results": sorted(c for c in calls - results if c),
        "results_without_calls": sorted(r for r in results - calls if r),
    }


def plan(messages: List[dict], keep_recent: int) -> Tuple[List[dict], List[dict]]:
    """``(summarise, keep)``: the older exchanges and the recent ones.

    ``summarise`` is empty when there is nothing older than what is kept.
    The latest exchange is always kept, even when it is the only one: the
    model is in the middle of it. An earlier exchange whose calls and
    results do not pair goes to the summary, where it becomes prose, so the
    kept span is always one a provider accepts. A previous summary is
    re-summarised, never kept, so summaries do not pile up.
    """
    groups = _exchanges(messages)
    keep_groups = groups[-keep_recent:]
    older = groups[: len(groups) - len(keep_groups)]
    while not older and len(keep_groups) > 1:
        older, keep_groups = keep_groups[:1], keep_groups[1:]
    broken = [
        group
        for position, group in enumerate(keep_groups)
        if not _paired(group)
        and not (position == len(keep_groups) - 1 and group[0].get("role") == "assistant")
    ]
    if broken:
        keep_groups = [g for g in keep_groups if all(g is not b for b in broken)]
        older = older + broken
    return [m for g in older for m in g], [m for g in keep_groups for m in g]


def clear_tool_results(messages: List[dict], keep_last: Optional[int]) -> List[dict]:
    """``messages`` with every tool message's content but the last
    ``keep_last`` replaced by :data:`CLEARED` (``None``: unchanged)."""
    if keep_last is None:
        return list(messages)
    tools = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    stale = set(tools[: max(0, len(tools) - keep_last)])
    return [{**m, "content": CLEARED} if i in stale else m for i, m in enumerate(messages)]


def summary_prompt(messages: List[dict]) -> str:
    """The summariser's prompt for the span being compacted."""
    lines = []
    for message in messages:
        role = message.get("role", "?")
        content = str(message.get("content") or "")
        if message.get("tool_calls"):
            names = ", ".join(
                str(c.get("name")) for c in message["tool_calls"] if isinstance(c, dict)
            )
            content = f"{content} [called: {names}]".strip()
        lines.append(f"{role}: {content}")
    return (
        "Summarise this conversation segment for an assistant that will continue the "
        "conversation without it. Keep decisions, facts established, identifiers and "
        "anything the user asked for that is not yet done. Drop pleasantries and superseded "
        "attempts. Write it as notes, not prose.\n\n" + "\n".join(lines)
    )


def dropped_note(count: int) -> str:
    """The summary when no summariser is set: say what went, never drop
    history silently."""
    return (
        f"{count} earlier messages were removed to stay within the context window, and no "
        "summary was generated. Ask the user to restate anything you need from before this "
        "point."
    )


def summary_item(summary: str, kept: List[dict]) -> Dict[str, Any]:
    """The session item a compaction appends: the summary and the
    messages kept verbatim after it, as the model will see them."""
    return {"role": "summary", "content": summary, "kept": list(kept)}


def summary_message(summary: str) -> Dict[str, Any]:
    return {"role": "user", "content": f"{SUMMARY_MARKER}\n{summary.strip()}"}


def view(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The conversation a session's items show the model: from the last
    summary on, the summary, what it kept, and every item after it."""
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if item.get("role") == "summary":
            return [summary_message(item["content"]), *item["kept"], *items[index + 1 :]]
    return list(items)
