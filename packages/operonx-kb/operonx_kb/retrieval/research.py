"""The research loop's logic: a researcher model reads what the searches found and
names the next search, until the passages answer the question (``kb.ask(searches=N)``).

One search answers a question that names what it asks about. A question in steps
("the unit that keeps its backups in Hải Phòng: what does the same team in another
region pay?") needs the first step's answer before it can search for the second;
here the model sees every passage found so far, numbered, and replies::

    {"search": "the next search, or an empty string when done", "keep": [2, 5]}

``keep`` names the passages the answer needs; they, not every hit, become the
answer's sources. The loop ends when the model asks for no search, repeats one,
or the search budget is spent.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

__all__ = ["RESEARCH_SYSTEM", "merge_hits", "research_request", "research_advance", "add_usage"]

#: Characters of one passage the researcher sees: a whole passage, bar an outsized one. A
#: shorter preview hid the fact the question needs in 11 of 12 misses on corpus_vi
#: (38% of its chunks run past 700 characters).
PASSAGE_CHARS = 2000

RESEARCH_SYSTEM = (
    "You research a question in a knowledge base, one search at a time. You see the "
    "question, the searches made so far and the numbered passages they found.\n"
    "Reply with JSON only, in this shape:\n"
    '{"search": "the next search, in plain words", "keep": [1, 4]}\n'
    "Rules:\n"
    '- "keep" lists every passage needed to answer the question, from all searches so far.\n'
    '- When the kept passages answer the question, reply with "search": "".\n'
    "- A question in steps is searched one step at a time: first find what the question "
    "describes without naming it (which unit, product or person it is), then search for "
    "what the question asks about it, using the name you found.\n"
    "- A comparison or a ranking needs a passage for every item compared: search for the "
    "items still missing.\n"
    "- Write searches in the language of the passages. Never repeat a search.\n"
    '- When no search is left, reply with "search": "" and the passages to keep.'
)


def merge_hits(
    prior: Optional[Sequence[Mapping[str, Any]]], new: Sequence[Mapping[str, Any]]
) -> List[dict]:
    """``prior`` then the hits of ``new`` not already in it (by chunk), so a passage keeps
    its number across searches."""
    out = [dict(h) for h in prior or []]
    seen = {h["chunk_id"] for h in out}
    for h in new:
        if h["chunk_id"] not in seen:
            seen.add(h["chunk_id"])
            out.append(dict(h))
    return out


def _passage(n: int, h: Mapping[str, Any]) -> str:
    label = " › ".join([h.get("title") or h["key"], *(h.get("heading_path") or [])[-2:]])
    text = " ".join(str(h.get("text") or "").split())
    if len(text) > PASSAGE_CHARS:
        text = text[: PASSAGE_CHARS - 1] + "…"
    return f"[{n}] {label}\n{text}"


def research_request(question: str, hits: Sequence[Mapping[str, Any]], searches: Sequence[str],
                     left: int) -> List[Dict[str, str]]:  # fmt: skip
    """The researcher's messages after ``searches`` found ``hits``; ``left`` searches remain."""
    made = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(searches)) or "(none)"
    passages = "\n\n".join(_passage(i + 1, h) for i, h in enumerate(hits)) or "(nothing found)"
    budget = (
        f"{left} more search{'es' if left != 1 else ''} allowed." if left else "No search is left."
    )
    user = (f"Question: {question}\n\nSearches so far:\n{made}\n\n{budget}\n\n"
            f"Passages:\n\n{passages}")  # fmt: skip
    return [{"role": "system", "content": RESEARCH_SYSTEM}, {"role": "user", "content": user}]


def _numbers(keep: Any, size: int) -> List[int]:
    if isinstance(keep, str):
        try:
            keep = json.loads(keep)
        except ValueError:
            keep = [p for p in keep.replace(",", " ").split()]
    out: List[int] = []
    for x in keep if isinstance(keep, list) else []:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= size and n not in out:
            out.append(n)
    return out


def research_advance(search: Any, keep: Any, hits: Sequence[Mapping[str, Any]],
                     searches: Sequence[str], max_searches: int) -> Dict[str, Any]:  # fmt: skip
    """Apply the researcher's reply: the next search (``None`` when done) and the kept hits.

    Done when the reply names no search, repeats one (case and spacing ignored), or
    ``max_searches`` are spent. Kept numbers outside the passages are ignored; when none
    is valid every hit is kept (the answer step's budget trims them).
    """
    nxt = " ".join(str(search or "").split())
    made = {" ".join(q.lower().split()) for q in searches}
    repeated = nxt.lower() in made
    done = not nxt or repeated or len(searches) >= max_searches
    chosen = _numbers(keep, len(hits))
    kept = [dict(hits[n - 1]) for n in chosen] if chosen else [dict(h) for h in hits]
    return {"next": None if done else nxt, "done": done, "kept": kept,
            "stats": {"searches": len(searches), "hits": len(hits), "kept": len(chosen) or len(hits),
                      "repeated": repeated}}  # fmt: skip


def add_usage(
    total: Optional[Mapping[str, Any]], usage: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    """``total`` plus ``usage``, field by numeric field (tokens, cost)."""
    out: Dict[str, Any] = dict(total or {})
    for key, value in (usage or {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = out.get(key, 0) + value
    return out
