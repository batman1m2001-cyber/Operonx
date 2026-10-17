"""``DELIVERY.md`` and the PR description: what a milestone built, and the evidence.

Written from the store and the BRD folder only, so it says what the
engine recorded, not what a model remembers: the cards merged, each row
with its verdict and the tests that prove it, the assumptions taken, the
questions the owner decided, what is known not to be done, and the cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List

from operonx_project.brd.records import matrix_status
from operonx_project.gitops import integration_branch
from operonx_project.model import CardState

if TYPE_CHECKING:
    from operonx_project.lifecycle import Lifecycle

__all__ = ["delivery_text", "pr_text"]


def _facts(life: "Lifecycle", mid: str) -> Dict:
    store = life.store
    ms = store.get_milestone(mid)
    ctx = store.ms_get(mid, "context") or {}
    final = store.ms_get(mid, "final") or {}
    base = store.ms_get(mid, "baseline") or {}
    cards = store.cards(mid)
    events = store.events(mid, limit=100_000)
    rows = ctx.get("rows") or []
    status: Dict[str, str] = {}
    if ctx.get("brd"):
        status = matrix_status(life.rt(mid).git.ensure_integration(mid) / ctx["brd"])
    return {"ms": ms, "ctx": ctx, "final": final, "base": base, "cards": cards, "events": events, "rows": rows,
            "status": status}  # fmt: skip


def delivery_text(life: "Lifecycle", mid: str) -> str:
    f = _facts(life, mid)
    ms, cards, rows, status = f["ms"], f["cards"], f["rows"], f["status"]
    done = [c for c in cards if c.state == CardState.DONE]
    lines: List[str] = [f"# Delivery: {ms.title} ({mid})", ""]
    if ms.goal:
        lines += [f"**Goal:** {ms.goal}", ""]
    mode = "W1 (spec → DAG)" if ms.policy.horizon == "all" else "W2 (BRD → checklist → board)"
    lines += [f"**Branch:** `{integration_branch(mid)}` · **Mode:** {mode} · **Cost:** ${ms.cost_usd:.2f} "
              "(notional on a Max plan)", ""]  # fmt: skip

    lines += ["## What was built", ""]
    for c in done:
        lines.append(f"- **{c.id}** {c.title}" + (f" — serves {', '.join(c.rows)}" if c.rows else ""))
    if not done:
        lines.append("- nothing was merged")
    lines.append("")

    if rows:
        lines += ["## Requirements, with evidence", "", "| Row | Status | Cards |", "|---|---|---|"]
        for r in rows:
            serving = [c.id for c in cards if r["id"] in c.rows and c.state != CardState.CANCELLED]
            lines.append(f"| {r['id']} | {status.get(r['id'], 'planned')} | {', '.join(serving) or '—'} |")
        lines += ["", "The tests each verified row cites are in the matrix, "
                  f"`{f['ctx'].get('brd')}/requirements/requirements_traceability.md`.", ""]  # fmt: skip

    assumptions = [e for e in f["events"] if e["kind"] == "assumption"]
    answers = [e for e in f["events"] if e["kind"] == "answer.applied"]
    lines += ["## Assumptions taken", ""]
    lines += [f"- {e['card']}: {e['data'].get('question') or ''} → {e['data'].get('answer') or e['data']}"
              for e in assumptions] or ["- none"]  # fmt: skip
    lines += ["", "## Decided by the owner", ""]
    lines += [f"- {e['card']}: {e['data'].get('choice') or ''} {e['data'].get('text') or ''}".rstrip()
              for e in answers] or ["- none"]  # fmt: skip

    limits: List[str] = []
    for c in cards:
        if c.state in (CardState.FAILED, CardState.CANCELLED, CardState.BLOCKED):
            limits.append(f"{c.id} ({c.title}) is {c.state.value}: {c.reason[:300]}")
    for h in f["ctx"].get("held") or []:
        limits.append(f"{h['id']} waits on {h.get('question') or 'a question'}: not in this milestone")
    unverified = [r["id"] for r in rows if status.get(r["id"]) != "implemented_verified"]
    if unverified:
        limits.append(f"rows not verified: {', '.join(unverified)}")
    for m in f["base"].get("missing") or []:
        limits.append(f"the check `{m['name']}` (`{m['cmd']}`) could not run here, so it was not used")
    lines += ["", "## Known limits", ""] + ([f"- {x}" for x in limits] or ["- none known"])
    old = [r["name"] for r in f["base"].get("results") or [] if not r.get("ok")]
    if old:
        lines += [f"- already failing before this milestone (not changed by it): {', '.join(old)}"]
    lines += ["", "## Checks", ""]
    lines += [f"- `{c['cmd']}`" for c in f["base"].get("checks") or []] or ["- none"]
    lines.append("")
    return "\n".join(lines)


def pr_text(life: "Lifecycle", mid: str) -> str:
    f = _facts(life, mid)
    ms, cards, rows, status = f["ms"], f["cards"], f["rows"], f["status"]
    done = [c for c in cards if c.state == CardState.DONE]
    verified = [r["id"] for r in rows if status.get(r["id"]) == "implemented_verified"]
    lines = [f"# {ms.title}", "", ms.goal or "", "", "## Summary", ""]
    lines += [f"- {c.id}: {c.title}" for c in done]
    if rows:
        lines += ["", f"Requirement rows verified: {len(verified)} of {len(rows)}."]
    lines += ["", "See `DELIVERY.md` for the evidence per row, the assumptions and the known limits.", ""]
    return "\n".join(lines)
