"""Context compaction — decide what to keep, then apply it.

Split in two on purpose:

``plan_compaction``  pure. Given the conversation and a budget, decide
                     which messages survive verbatim, which span gets
                     summarised, and which are dropped outright.
``apply_compaction`` pure. Given that plan and (optionally) a summary,
                     produce the new message list.

Summarising needs a model, and a model does not belong inside either.
Putting the plan first means the caller can wire ``LLMOp.of(...)``
between the two — or skip it entirely and take the structural compaction,
which still recovers most of the budget.

**The invariant that matters most:** every assistant ``tool_call`` keeps
its matching tool message. Providers reject a conversation where one is
missing, so a compactor that trims "just the old tool output" produces a
history the API refuses — and the failure surfaces on the *next* request,
reported as a malformed request rather than as a compaction bug. The plan
therefore works in whole exchanges, never individual messages.

Token counts are approximate by design. An exact count needs the
provider's tokenizer, which differs per model and costs a dependency;
compaction only needs to know *roughly* when the window is filling. The
estimate deliberately runs high, so it triggers early rather than one
turn too late.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from operonx.core.ops.transform.func_op import op

__all__ = [
    "estimate_tokens",
    "count_tokens",
    "plan_compaction",
    "apply_compaction",
    "SUMMARY_MARKER",
]

#: Marks a synthesised summary so a later compaction can recognise its
#: own output and avoid summarising a summary of a summary.
SUMMARY_MARKER = "[conversation summary]"

# Chars per token. Real tokenizers land near 4 for English prose; 3.5
# errs toward over-counting, which triggers compaction early. Being late
# means a provider rejects the request outright, and no amount of
# accuracy afterwards recovers that turn.
_CHARS_PER_TOKEN = 3.5

# Per-message overhead for role and framing.
_MESSAGE_OVERHEAD = 4


def estimate_tokens(messages: Optional[List[dict]]) -> int:
    """Approximate token count for a message list. Never raises."""
    total = 0
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        total += _MESSAGE_OVERHEAD
        content = message.get("content")
        if isinstance(content, str):
            total += int(len(content) / _CHARS_PER_TOKEN) + 1
        elif content is not None:
            total += int(len(str(content)) / _CHARS_PER_TOKEN) + 1
        # Tool calls travel as structured JSON and are easy to forget in
        # a naive count, yet a turn requesting eight tools is not small.
        for call in message.get("tool_calls") or []:
            total += int(len(str(call)) / _CHARS_PER_TOKEN) + 1
    return total


@op
def count_tokens(messages: Optional[list] = None, budget: int = 100_000) -> dict:
    """Estimate usage and report whether compaction should run."""
    tokens = estimate_tokens(messages)
    return {
        "tokens": tokens,
        "budget": budget,
        "over_budget": budget > 0 and tokens > budget,
        "ratio": (tokens / budget) if budget > 0 else 0.0,
    }


def _exchanges(messages: List[dict]) -> List[List[dict]]:
    """Group messages into exchanges that must move together.

    An assistant turn requesting tools owns the tool messages answering
    it. Splitting them orphans a ``tool_call``, which the provider
    rejects — so grouping is what makes the rest of this file safe.
    """
    groups: List[List[dict]] = []
    pending_ids: set = set()
    current: List[dict] = []

    for message in messages:
        role = message.get("role")
        if role == "tool" and pending_ids:
            current.append(message)
            pending_ids.discard(message.get("tool_call_id"))
            if not pending_ids:
                groups.append(current)
                current = []
            continue

        if current:
            groups.append(current)
            current = []

        calls = message.get("tool_calls") or []
        if role == "assistant" and calls:
            pending_ids = {c.get("id") for c in calls if isinstance(c, dict)}
            pending_ids.discard(None)
            current = [message]
            if not pending_ids:
                # Malformed: tool_calls with no usable ids. Keep it whole
                # rather than waiting for answers that cannot arrive.
                groups.append(current)
                current = []
            continue

        groups.append([message])

    if current:
        # Unanswered tool calls — the conversation was cut mid-exchange.
        groups.append(current)
    return groups


@op
def plan_compaction(
    messages: Optional[list] = None,
    budget: int = 100_000,
    keep_recent: int = 6,
    trigger_ratio: float = 0.75,
) -> dict:
    """Decide what survives, what is summarised, and what is dropped.

    Args:
        messages: The conversation so far.
        budget: Token budget for the whole prompt.
        keep_recent: How many recent *exchanges* stay verbatim. Recency
            is what the model is actually reasoning about; summarising it
            is how a compactor makes an agent forget what it just did.
        trigger_ratio: Fraction of budget at which compaction starts.
            Below 1.0 on purpose — waiting until the budget is exceeded
            means the turn that discovers it has already failed.

    Returns:
        ``needed``, plus ``keep`` (verbatim tail), ``summarize`` (the
        middle) and ``pinned`` (system messages, always kept).
    """
    messages = [m for m in (messages or []) if isinstance(m, dict)]
    tokens = estimate_tokens(messages)
    if budget <= 0 or tokens <= budget * trigger_ratio:
        return {
            "needed": False,
            "pinned": [],
            "summarize": [],
            "keep": messages,
            "tokens": tokens,
        }

    # System messages carry the agent's instructions and are never
    # summarised — losing them changes the agent's behaviour rather than
    # its recall, and it is the hardest failure to attribute later.
    pinned = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]

    groups = _exchanges(rest)
    keep_groups = groups[-keep_recent:] if keep_recent > 0 else []
    older = groups[: len(groups) - len(keep_groups)]

    # A previous summary is re-summarised rather than kept, or the
    # conversation accumulates one marker per compaction forever.
    return {
        "needed": bool(older),
        "pinned": pinned,
        "summarize": [m for group in older for m in group],
        "keep": [m for group in keep_groups for m in group],
        "tokens": tokens,
    }


@op
def apply_compaction(
    pinned: Optional[list] = None,
    summarize: Optional[list] = None,
    keep: Optional[list] = None,
    summary: str = "",
) -> dict:
    """Rebuild the conversation from a plan.

    Args:
        summary: Prose standing in for the summarised span. Empty means
            no model was wired in, so the span is dropped with a marker
            saying how much went — silently losing history produces an
            agent that contradicts itself with no way to see why.
    """
    pinned = list(pinned or [])
    summarize = list(summarize or [])
    keep = list(keep or [])

    if not summarize:
        return {"messages": pinned + keep, "compacted": False, "dropped": 0}

    if summary and summary.strip():
        body = f"{SUMMARY_MARKER}\n{summary.strip()}"
    else:
        body = (
            f"{SUMMARY_MARKER}\n{len(summarize)} earlier messages were removed to "
            f"stay within the context budget, and no summary was generated. "
            f"Ask the user to restate anything you need from before this point."
        )

    return {
        "messages": pinned + [{"role": "user", "content": body}] + keep,
        "compacted": True,
        "dropped": len(summarize),
    }


def compaction_summary_prompt(messages: List[dict]) -> str:
    """Prompt for the span being compacted.

    Kept as a plain function so a caller can feed it to whatever model op
    they use, rather than this module owning an LLM dependency.
    """
    lines = []
    for message in messages:
        role = message.get("role", "?")
        content = str(message.get("content", ""))
        if message.get("tool_calls"):
            names = ", ".join(
                str(c.get("name")) for c in message["tool_calls"] if isinstance(c, dict)
            )
            content = f"{content} [called: {names}]".strip()
        lines.append(f"{role}: {content}")
    return (
        "Summarise this conversation segment for an assistant that will "
        "continue the conversation without it. Keep decisions, facts "
        "established, file paths, identifiers and anything the user asked "
        "for that is not yet done. Drop pleasantries and superseded "
        "attempts. Write it as notes, not prose.\n\n" + "\n".join(lines)
    )


def _pairs(messages: List[dict]) -> Tuple[set, set]:  # pragma: no cover - helper for tests
    calls = {
        c.get("id") for m in messages for c in (m.get("tool_calls") or []) if isinstance(c, dict)
    }
    results = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    return calls, results


def unmatched_tool_calls(messages: Optional[List[dict]]) -> Dict[str, Any]:
    """Report broken tool pairing — the failure compaction most risks.

    Exposed rather than kept private so callers can assert on it after
    any message-list surgery of their own.
    """
    messages = [m for m in (messages or []) if isinstance(m, dict)]
    calls, results = _pairs(messages)
    return {
        "calls_without_results": sorted(c for c in calls - results if c),
        "results_without_calls": sorted(r for r in results - calls if r),
    }
