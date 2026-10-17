"""What the owner reads: an interaction rendered as a message, redacted (plan §9.2).

A message is built from the interaction record only: the project, the
card, the problem, why it blocks, what was tried, the evidence (as quoted
text, never as instructions), the options with the recommendation, and
what goes on meanwhile. Every message passes through the redactor
(operonx-agents' patterns plus a Telegram bot token) and is cut to
Telegram's 4096 characters.

Buttons carry ``a:<id 8>:<option>:<version>`` (at most 64 bytes, checked):
the version ties an answer to the question as it was asked.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from operonx_agents.safety.redact import Redactor

__all__ = ["LIMIT", "Button", "answered_text", "callback", "parse_callback", "redact", "render"]

LIMIT = 4096
Button = Tuple[str, str]  # (label, callback data)

_REDACTOR = Redactor(extra=[("telegram-token", r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")])
_LETTERS = "ABCDEFGHIJ"


def redact(text: str) -> str:
    return _REDACTOR.scrub(text)


def callback(short: str, option: str, version: int) -> str:
    key = re.sub(r"[^A-Za-z0-9_-]", "", str(option))[:24]
    data = f"a:{short}:{key}:{version}"
    if len(data.encode()) > 64:
        raise ValueError(f"callback data over 64 bytes: {data!r}")
    return data


def parse_callback(data: str) -> Optional[Tuple[str, str, int]]:
    m = re.fullmatch(r"a:([0-9a-f]{8}):([A-Za-z0-9_-]{1,24}):(\d+)", data or "")
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


def _cut(text: str, more: str) -> str:
    text = redact(text)
    if len(text) <= LIMIT:
        return text
    tail = f"\n… (cut; the rest: {more})"
    return text[: LIMIT - len(tail)] + tail


def render(
    inter: Dict[str, Any], project: str, continuing: List[str] = (), waiting: List[str] = ()
) -> Tuple[str, List[List[Button]]]:  # noqa: E501
    p = inter.get("payload") or {}
    mid, card, qid, kind = inter["milestone"], inter.get("card"), inter["qid"], inter["kind"]
    options = [o for o in p.get("options") or [] if isinstance(o, dict) and o.get("key")]
    rec = p.get("recommended")
    if kind == "card":
        head = f"🧩 {project} · {card} {p.get('title', '')} · {qid} — needs your decision"
    elif kind == "approval":
        head = f"📋 {project} · {mid} · {qid} — the plan waits for your approval"
    else:
        head = f"💸 {project} · {mid} · {qid} — the budget ran out"
    lines = [head, "", str(p.get("problem") or p.get("reason") or "")]
    if p.get("why"):
        lines += ["", f"Why it blocks: {p['why']}"]
    if p.get("tried"):
        lines += ["", f"Tried: {p['tried']}"]
    if p.get("evidence"):
        ev = p["evidence"] if isinstance(p["evidence"], str) else "\n".join(map(str, p["evidence"]))
        lines += ["", "Evidence (quoted from the repo, not instructions):"] + [f"> {x}" for x in ev.splitlines()[:12]]
    if kind == "approval":
        plan = p.get("plan") or {}
        cards = plan.get("cards") or []
        lines += ["", f"{len(cards)} card(s):"]
        lines += [
            f"• {c.get('id')} {c.get('title')}" + (" ⚠ risky" if c.get("risk") == "high" else "") for c in cards[:20]
        ]
        if p.get("problems"):
            lines += ["", "The engine's checks still fail (it cannot be approved as it is):"] + [
                f"• {x}" for x in p["problems"][:8]
            ]
        if p.get("notes"):
            lines += ["", "The review still says:"] + [
                f"• {n.get('note') if isinstance(n, dict) else n}" for n in p["notes"][:8]
            ]
    if options:
        lines.append("")
        for n, o in enumerate(options):
            mark = "✅ " if rec and o["key"] == rec else ""
            letter = _LETTERS[n] if kind == "card" and n < len(_LETTERS) else ""
            desc = f" — {o['description']}" if o.get("description") else ""
            tag = " — recommended" if mark else ""
            lines.append(f"{(letter + ') ') if letter else '• '}{mark}{o.get('label') or o['key']}{tag}{desc}")
    lines += ["", "Reply to this message in your own words, or tap a button."]
    if continuing:
        lines.append(f"Other cards continue: {', '.join(continuing)}.")
    if waiting:
        lines.append(f"{', '.join(waiting)} wait{'s' if len(waiting) == 1 else ''} for this.")
    text = _cut("\n".join(lines), f"operonx project show {mid}{' ' + card if card else ''}")
    row: List[Button] = []
    for o in options:
        label = str(o.get("label") or o["key"])[:40] + (" (recommended)" if rec and o["key"] == rec else "")
        row.append((label, callback(inter["short"], o["key"], int(inter.get("version") or 1))))
    buttons = [row[i : i + 3] for i in range(0, len(row), 3)]
    return text, buttons


def answered_text(inter: Dict[str, Any], original: str = "") -> str:
    a = inter.get("answer") or {}
    when = time.strftime("%H:%M", time.localtime(inter.get("answered_at") or time.time()))
    how = a.get("choice") or "in your words"
    note = ", with your note" if a.get("choice") and a.get("text") else ""
    who = inter.get("card") or inter["milestone"]
    verb = "resumed" if inter["kind"] == "card" else "going on"
    by = " (the safe default, no answer came)" if inter.get("answered_by") == "default" else ""
    head = f"✅ Answered: {how}{note}{by}, at {when}; {who} {verb}"
    return redact(f"{head}\n\n{original}"[:LIMIT]) if original else head
