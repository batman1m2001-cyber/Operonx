"""The ops of ``final_flow``: everything checked once more on the integration branch.

Each op returns its ``gaps``: ``{"card", "why", "reason"}``, where ``card``
is the card the gap re-opens, or ``None`` when no single card is
responsible: the lifecycle then adds a card to fix it. It re-opens or adds
cards (bounded by ``max_reopens``) or delivers.

- ``full_checks``: the repo's checks against the milestone's baseline (an
  empty repo's baseline is empty: every failure is new);
- ``heldout_all``: every card's held-out tests (V2), names and messages only;
- ``whole_review``: one session on the whole milestone diff, for what no
  single card saw; a blocking finding re-opens the card whose paths hold
  its file;
- ``record_rows``: each row in scope is ``implemented_verified`` when every
  card serving it is done and its acceptance tests pass here, citing them;
  the BRD folder is committed on the integration branch;
- ``brd_checked``: ``operonx project brd check`` on that folder.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from operonx import child, op

from operonx_project.brd.folder import refresh_summary
from operonx_project.brd.records import set_matrix_row
from operonx_project.checks import CheckResult, compare, run_checks
from operonx_project.dag import paths_overlap
from operonx_project.flows.stage import stage_call, stage_request
from operonx_project.model import Card, CardState
from operonx_project.prompts import REVIEWER_SCHEMA, milestone_review_prompt
from operonx_project.review import check_findings, heldout_failures
from operonx_project.runtime import Runtime, get_runtime

__all__ = ["brd_checked", "full_checks", "heldout_all", "record_rows", "verdict", "whole_review"]


def _gap(card: Optional[str], why: str, reason: str) -> Dict[str, Any]:
    return {"card": card, "why": why, "reason": reason[:2000]}


def _brd(rt: Runtime, mid: str) -> Optional[Path]:
    ctx = rt.store.ms_get(mid, "context") or {}
    if not ctx.get("brd"):
        return None
    return rt.git.ensure_integration(mid) / ctx["brd"]


def _done(rt: Runtime, mid: str) -> List[Card]:
    return [c for c in rt.store.cards(mid) if c.state == CardState.DONE]


@op
async def full_checks(key: str, mid: str) -> dict:
    rt = get_runtime(key)
    integ = rt.git.ensure_integration(mid)
    base = rt.store.ms_get(mid, "baseline") or {}
    async with child("checks", inputs={"checks": [c.name for c in rt.checks]}, op_type="checks") as step:
        results = await run_checks(rt.checks, integ, rt.env)
        cmp = compare([CheckResult(**r) for r in base.get("results") or []], results)
        step.outputs = {"new": cmp.new, "old": cmp.old, "fixed": cmp.fixed}
    gaps = []
    if cmp.new:
        tails = "\n".join(f"$ {r.name}\n{r.tail[-1200:]}" for r in results if not r.ok)
        gaps.append(_gap(None, "checks", f"on the integration branch these checks fail: {', '.join(cmp.new)}\n{tails}"))
    return {"gaps": gaps}


@op
async def heldout_all(key: str, mid: str) -> dict:
    rt = get_runtime(key)
    integ = rt.git.ensure_integration(mid)
    gaps = []
    for card in _done(rt, mid):
        held = card.detail.get("heldout") or {}
        if not held.get("files"):
            continue
        check, inside = rt.heldout_check(f"heldout-{card.id}", Path(held["dir"]), integ)
        async with child(f"heldout-{card.id}", inputs={"files": len(held["files"])}, op_type="checks") as step:
            (res,) = await run_checks([check], integ, rt.env)
            names = [] if res.ok else heldout_failures(res.tail, Path(inside))
            step.outputs = {"ok": res.ok, "failing": names}
        if not res.ok:
            names = names or [f"the held-out run failed (exit {res.exit_code}) without naming a test"]
            gaps.append(_gap(card.id, "heldout", "held-out tests fail on the integration branch:\n" + "\n".join(names)))
    return {"gaps": gaps}


@op
async def whole_review(key: str, mid: str) -> dict:
    rt = get_runtime(key)
    ms = rt.store.get_milestone(mid)
    integ = rt.git.ensure_integration(mid)
    base = (rt.store.ms_get(mid, "baseline") or {}).get("sha")
    if not base:
        return {"gaps": []}
    changed = rt.git.changed(integ, base)
    if not changed:
        return {"gaps": []}
    ctx = rt.store.ms_get(mid, "context") or {}
    cards = rt.store.cards(mid)
    summary = [{"id": c.id, "title": c.title, "state": c.state.value, "paths": c.paths} for c in cards]
    prompt = milestone_review_prompt(ms.goal, ctx.get("rows") or [], rt.git.diff(integ, base), summary)
    req = stage_request(rt, mid, "reviewer", "milestone_reviewer", prompt, integ, "milestone-review",
                        schema=REVIEWER_SCHEMA)  # fmt: skip
    res = await stage_call(rt, mid, "milestone-review", req)
    if not res.ok or not isinstance(res.output, dict):
        rt.store.note(mid, "final.review_failed", {"status": res.status, "error": res.error[:300]})
        return {
            "gaps": [
                _gap(None, "review_unfinished", f"the milestone review did not finish ({res.status}): {res.error}")
            ]
        }
    blocking, _ = check_findings(res.output.get("findings") or [], integ, changed)
    gaps = []
    for f in blocking:
        done = [c for c in cards if c.state == CardState.DONE and c.kind != "init"]
        owner = next((c.id for c in done if paths_overlap(c.paths, [f["file"]])), None)
        if owner is None:  # a finding about a card's missing change cites no file of it (tipcalc m1/C6): its name
            named = {c.id for c in done if re.search(rf"\b{re.escape(c.id)}\b", f"{f['title']} {f.get('detail', '')}")}
            owner = named.pop() if len(named) == 1 else None
        gaps.append(_gap(owner, "review", f"{f['severity']} at {f['file']}:{f.get('line')}: {f['title']}\n"
                                          f"{f.get('detail', '')}"))  # fmt: skip
    return {"gaps": gaps}


def _tests_in(root: Path, rel_dir: str) -> List[str]:
    out = []
    d = root / rel_dir
    for f in sorted(d.rglob("test_*.py")) if d.is_dir() else []:
        rel = f.relative_to(root).as_posix()
        for name in re.findall(r"^(?:async\s+)?def\s+(test_\w+)", f.read_text(encoding="utf-8"), re.M):
            out.append(f"{rel}::{name}")
    return out


@op
async def record_rows(key: str, mid: str) -> dict:
    rt = get_runtime(key)
    brd = _brd(rt, mid)
    ctx = rt.store.ms_get(mid, "context") or {}
    rows = [r["id"] for r in ctx.get("rows") or []]
    if brd is None or not rows:
        return {"gaps": [], "verified": []}
    integ = rt.git.ensure_integration(mid)
    cards = rt.store.cards(mid)
    gaps: List[Dict[str, Any]] = []
    verified: List[str] = []
    passed: Dict[str, bool] = {}
    for rid in rows:
        serving = [c for c in cards if rid in c.rows and c.state != CardState.CANCELLED]
        if not serving:
            continue  # no card: a known limit, said in DELIVERY.md
        if any(c.state != CardState.DONE for c in serving):
            continue  # a card failed or is not merged: not verified, said in DELIVERY.md
        tests: List[str] = []
        ok = True
        for c in serving:
            acc = c.detail.get("acceptance") or {}
            if not acc.get("files"):
                continue
            if c.id not in passed:
                check = rt.test_check(f"acceptance-{c.id}", rt.acceptance_cmd.format(dir=acc["dir"]))
                (res,) = await run_checks([check], integ, rt.env)
                passed[c.id] = res.ok
                if not res.ok:
                    gaps.append(_gap(c.id, "acceptance", f"its acceptance tests fail on the integration branch:\n"
                                                         f"{res.tail[-1500:]}"))  # fmt: skip
            ok = ok and passed[c.id]
            tests += _tests_in(integ, acc["dir"])
        if ok and tests:
            code = ""
            for p in sorted({p for c in serving for p in c.paths}):  # whole paths, never one cut in half
                if len(code) + len(p) > 200:
                    code += ", …"
                    break
                code = f"{code}, {p}" if code else p
            ev = f"{mid}: " + ", ".join(f"{c.id} done" for c in serving)
            if set_matrix_row(brd, rid, "implemented_verified", tests, code=code, evidence=ev):
                verified.append(rid)
    if verified:
        refresh_summary(brd)
        sha = rt.git.commit(integ, f"{mid}: requirement rows verified ({len(verified)})")
        rt.store.note(mid, "final.rows", {"verified": verified, "commit": sha})
    return {"gaps": gaps, "verified": verified}


@op
async def brd_checked(key: str, mid: str) -> dict:
    from operonx_project.brd.checker import check

    rt = get_runtime(key)
    brd = _brd(rt, mid)
    if brd is None or not brd.exists():
        return {"gaps": []}
    errors, _ = check(brd)
    if not errors:
        return {"gaps": []}
    return {"gaps": [_gap(None, "brd", "operonx project brd check: " + "; ".join(errors[:20]))]}


@op
def verdict(key: str, mid: str, checks: list, heldout: list, review: list, rows: list, brd: list,
            verified: list) -> dict:  # fmt: skip
    rt = get_runtime(key)
    gaps = [*checks, *heldout, *review, *rows, *brd]
    rt.store.ms_put(mid, "final", {"gaps": gaps, "verified": verified})
    rt.store.note(mid, "final.verdict", {"gaps": len(gaps), "verified": len(verified)})
    return {"gaps": gaps, "verified": verified}  # a literal: operonx reads an op's outputs from it
