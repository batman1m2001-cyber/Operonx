"""Where the project stands, in one document (PROJECT_MODE_PLAN D2).

:func:`status` reads the checklist, the plan docs (:mod:`operonx_project.plans`), the board
(``docs/brd/board.md``) when the project has one, the BRD's open questions and the last commits, and returns
one dict the studio's screen and its assistant both read::

    checklist   counts by status, the open rows (neither verified nor out of scope)
    plans       plan docs, in progress first, then planned, then the rest;
                ``unmarked``: the ``*_PLAN.md`` files with no status block yet
    board       the board's cards (:func:`operonx_project.board.view`), or ``None``
    needs_you   the BRD's open questions and the cards that need the owner
    working_on  the cards doing and the plans in progress, each with one sentence written here from the data
    changes     the last commits, newest first
    standard    the standard, item by item (brd, check, plans): ok, a sentence, the fix

It only reads. Every sentence is made by code from the data; no model writes any of it.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from operonx_project.board import PATH, lane, read, view
from operonx_project.brd.records import MATRIX, STATUSES, _split, load_questions
from operonx_project.plans import find

__all__ = ["CLOSED", "STANDARD", "standard", "status", "text"]

#: Row statuses that need no more work.
CLOSED = ("implemented_verified", "out_of_scope_demo", "not_applicable")
_ORDER = {"in_progress": 0, "plan": 1, "parked": 2, "done": 3}
_WHAT = ("requirement", "metric", "title", "what")  # the column that says what a row is, by its header
_NOTE = ("gap", "note", "evidence")  # in this order: "Evidence, gaps" and "Notes" before "Tests / evidence"


def _matrix(brd: Path) -> List[Dict[str, str]]:
    """Every matrix row as ``{id, status, what, note}``, read by each table's own header."""
    f = brd / MATRIX
    if not f.is_file():
        return []
    rows: List[Dict[str, str]] = []
    header: List[str] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            header = []
            continue
        cells = _split(line)
        if not header:
            header = [c.lower() for c in cells]
            continue
        if set(line.replace("|", "").strip()) <= set("-: "):
            continue
        found = [c for c in cells if c in STATUSES]
        if header[0] != "id" or len(found) != 1:
            continue

        def col(names) -> str:
            for n in names:
                i = next((i for i, h in enumerate(header) if n in h), None)
                if i is not None and i < len(cells):
                    return cells[i]
            return ""

        rows.append({"id": cells[0], "status": found[0], "what": col(_WHAT)[:200], "note": col(_NOTE)[:300]})
    return rows


def _git_log(repo: Path, n: int) -> List[Dict[str, str]]:
    try:
        out = subprocess.run(["git", "-C", str(repo), "log", f"-n{n}", "--format=%h%x00%cI%x00%an%x00%s"],
                             capture_output=True, text=True, timeout=10)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    keys = ("sha", "at", "author", "subject")
    return [dict(zip(keys, line.split("\x00"))) for line in out.stdout.splitlines() if line.count("\x00") == 3]


#: The standard, in order (PROJECT_STANDARD_PLAN D1): what a project needs for the studio and the engine to read
#: it, each with what it is called and what makes it true.
STANDARD = (
    ("brd", "a checklist", "docs/brd with its requirements and the checklist matrix (operonx-project adopt "
                           "writes one from the code)"),
    ("check", "brd check passes", "fix what operonx-project brd check reports; a summary table with sets of its "
                                  "own names them in docs/brd/brd.toml [[groups]] (name, pattern)"),
    ("plans", "every plan says where it stands", "a status block at the top of each docs/*_PLAN.md (title, status, "
                                                 "phases with their status, rows, updated)"),
)

_CHECKED: Dict[Path, Any] = {}  # brd dir → (the files' signature, the errors): the check re-runs when a file changes


def _signature(brd: Path) -> Any:
    from operonx_project.brd.checker import BrdConfig

    cfg = BrdConfig.load(brd)
    files = [*brd.rglob("*")]
    if cfg.tests.is_dir():
        files += cfg.tests.rglob("test_*.py")
    files += (cfg.root / "docs").glob("**/*_PLAN.md")
    sig = []
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        sig.append((str(f), st.st_mtime_ns, st.st_size))
    return tuple(sorted(sig))


def _check_errors(brd: Path) -> List[str]:
    from operonx_project.brd.checker import check

    sig = _signature(brd)
    hit = _CHECKED.get(brd)
    if hit and hit[0] == sig:
        return hit[1]
    try:
        errors, _ = check(brd)
    except Exception as exc:  # noqa: BLE001 — a checker that cannot read the folder is itself the finding
        errors = [f"brd check could not run: {exc}"]
    _CHECKED[brd] = (sig, errors)
    return errors


def standard(repo: Path, doc: Dict[str, Any], brd: str = "docs/brd") -> List[Dict[str, Any]]:
    """The project against the standard, one entry per item of :data:`STANDARD`: ``key``, ``name``, ``ok``,
    ``line`` (where it stands, in words) and ``fix`` (what makes it true)."""
    brd_dir = Path(repo) / brd
    has_brd = doc["checklist"]["total"] > 0
    out: List[Dict[str, Any]] = []
    for key, name, fix in STANDARD:
        if key == "brd":
            ok = has_brd
            line = f"{doc['checklist']['total']} rows in {doc['checklist']['file']}" if ok else f"no checklist in {brd}"
        elif key == "check":
            errors = _check_errors(brd_dir) if has_brd else []
            ok = has_brd and not errors
            line = ("no checklist to check" if not has_brd else "0 errors" if ok
                    else f"{len(errors)} error{'s' if len(errors) > 1 else ''}, first: {errors[0]}")  # fmt: skip
        else:
            bad = len(doc["plan_errors"]) + len(doc["unmarked"])
            ok = not bad
            line = (f"{len(doc['plans'])} plans, each with its status" if ok else
                    ", ".join(x for x in (f"{len(doc['unmarked'])} with no status block" if doc["unmarked"] else "",
                                          f"{len(doc['plan_errors'])} with a wrong one" if doc["plan_errors"] else "")
                              if x))  # fmt: skip
        out.append({"key": key, "name": name, "ok": ok, "line": line, "fix": "" if ok else fix})
    return out


def _sentence(plan: Dict[str, Any]) -> str:
    """One line of what a plan in progress is doing, from its data."""
    phases = {p["id"]: p for p in plan["phases"]}
    cur = phases.get(plan["current"] or "")
    if not plan["total"]:
        return "in progress (no phases listed)"
    if cur is None:
        return f"{plan['done']} of {plan['total']} phases done, none in progress"
    n = list(phases).index(cur["id"]) + 1
    verb = "" if cur["status"] == "in_progress" else "next: "
    return f"{verb}{cur['id']}, phase {n} of {plan['total']}: {cur['title'] or cur['id']}"


def status(repo: Path, changes: int = 15, brd: str = "docs/brd") -> Dict[str, Any]:
    """The project at *repo*, in one document (see the module's docstring)."""
    repo = Path(repo).resolve()
    brd_dir = repo / brd
    rows = _matrix(brd_dir)
    counts = Counter(r["status"] for r in rows)
    open_rows = [r for r in rows if r["status"] not in CLOSED]
    checklist = {"total": len(rows), "verified": counts.get("implemented_verified", 0),
                 "counts": {s: counts[s] for s in STATUSES if counts.get(s)}, "open": open_rows,
                 "file": f"{brd}/{MATRIX}" if rows else ""}  # fmt: skip

    docs, plan_errors, plan_warnings = find(repo)
    plans = [{**p.as_dict(), "source": "doc"} for p in docs]
    plans.sort(key=lambda p: p["updated"] if p["status"] == "done" else "", reverse=True)  # done: newest first
    plans.sort(key=lambda p: _ORDER.get(p["status"], 9))  # stable: in progress, planned, parked, done
    cards = read(repo)
    board = view(cards) if cards is not None else None
    done = sum(1 for c in cards or [] if c.state == "done")
    working_on = [{"title": f"{c.id} · {c.title}", "source": "board", "path": PATH, "card": c.id,
                   "sentence": f"doing · {lane(c.files)} · {done} of {len(cards)} cards done"}
                  for c in cards or [] if c.state == "doing"]  # fmt: skip
    working_on += [{"title": p["title"], "source": p["source"], "path": p["path"], "sentence": _sentence(p)}
                   for p in plans if p["status"] == "in_progress"]  # fmt: skip

    needs_you: List[Dict[str, Any]] = []
    for q in load_questions(brd_dir):
        if q.state.lower().startswith("open"):
            needs_you.append({"source": "brd", "id": q.id, "kind": q.type, "text": q.topic, "state": q.state})
    for c in cards or []:
        if c.state == "needs you":
            needs_you.append({"source": "board", "id": c.id, "kind": "card", "text": c.note or c.title,
                              "state": "open"})  # fmt: skip

    unmarked = [w.split(": ", 1)[0] for w in plan_warnings]
    doc = {"repo": str(repo), "checklist": checklist, "plans": plans, "plan_errors": plan_errors,
           "unmarked": unmarked, "board": board, "needs_you": needs_you, "working_on": working_on,
           "changes": _git_log(repo, changes)}  # fmt: skip
    doc["standard"] = standard(repo, doc, brd)
    return doc


def text(doc: Dict[str, Any], changes: int = 5) -> str:
    """The document in plain lines: what ``operonx-project status`` prints and the assistant reads."""
    out: List[str] = []
    c = doc["checklist"]
    if c["total"]:
        out.append(f"Checklist: {c['verified']} of {c['total']} verified, {len(c['open'])} open")
        for r in c["open"]:
            out.append(f"  {r['id']}  {r['status']}  {r['what'] or r['note']}"[:220])
    else:
        out.append("Checklist: none (no docs/brd matrix)")
    b = doc.get("board")
    if b:
        out.append("Board: " + ", ".join(f"{len(ids)} {s}" for s, ids in b["columns"].items() if ids))
        for card in b["cards"]:
            if card["state"] != "done":
                waits = f"  (after {', '.join(card['waits'])})" if card["waits"] else ""
                out.append(f"  {card['id']:<4} {card['state']:<9} {card['lane']:<8} {card['title']}{waits}"[:220])
    if doc["working_on"]:
        out.append("Working on:")
        for w in doc["working_on"]:
            out.append(f"  {w['title']} — {w['sentence']}")
    needs = doc["needs_you"]
    out.append(f"Needs you: {len(needs)}" if needs else "Needs you: nothing")
    for q in needs:
        out.append(f"  {q['id']}  {q['kind']}  {q['text']}"[:220])
    counts = Counter(p["status"] for p in doc["plans"])
    if doc["plans"]:
        out.append("Plans: " + ", ".join(f"{counts[s]} {s.replace('_', ' ')}" for s in _ORDER if counts.get(s)))
        for p in doc["plans"]:
            if p["status"] != "done":
                out.append(f"  {p['status']:<12} {p['done']}/{p['total']}  {p['title']}  ({p['path']})")
    for e in doc["plan_errors"]:
        out.append(f"  ERROR {e}")
    if doc["unmarked"]:
        out.append(f"Plan docs with no status block ({len(doc['unmarked'])}): {', '.join(doc['unmarked'])}")
    if doc["changes"]:
        out.append("Recent changes:")
        for ch in doc["changes"][:changes]:
            out.append(f"  {ch['sha']} {ch['at'][:10]}  {ch['subject']}"[:200])
    std = doc.get("standard") or []
    missing = [x for x in std if not x["ok"]]
    out.append(f"Standard: {len(std) - len(missing)} of {len(std)}" + ("" if missing else ", all met"))
    for x in missing:
        out.append(f"  missing {x['key']}: {x['line']}. Fix: {x['fix']}"[:400])
    return "\n".join(out) + "\n"
