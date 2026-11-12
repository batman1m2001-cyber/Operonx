"""Where the project stands, in one document (PROJECT_MODE_PLAN D2).

:func:`status` reads the checklist, the board (``docs/brd/board.md``, cut into cycles: CYCLES_PLAN) when the
project has one and the last commits, and returns one dict the studio's screen and its assistant both read::

    checklist   counts by status, the open rows (neither verified nor out of scope)
    board       the board's cycles and cards (:func:`operonx_project.board.view`), or ``None``
    needs_you   the cards in *needs you*
    working_on  the cards in *doing*, each with one sentence written here from the data
    changes     the last commits, newest first
    standard    the standard, item by item (brd, check): ok, a sentence, the fix

A plan doc says why and how; where the work stands is its cycle on the board.

It only reads. Every sentence is made by code from the data; no model writes any of it.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from operonx_project.board import PATH, lane, read_all, view
from operonx_project.brd.records import MATRIX, STATUSES, _split

__all__ = ["CLOSED", "STANDARD", "standard", "status", "text"]

#: Row statuses that need no more work.
CLOSED = ("implemented_verified", "out_of_scope_demo", "not_applicable")
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
)

_CHECKED: Dict[Path, Any] = {}  # brd dir → (the files' signature, the errors): the check re-runs when a file changes


def _signature(brd: Path) -> Any:
    from operonx_project.brd.checker import BrdConfig

    cfg = BrdConfig.load(brd)
    files = [*brd.rglob("*")]
    if cfg.tests.is_dir():
        files += cfg.tests.rglob("test_*.py")
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
        else:
            errors = _check_errors(brd_dir) if has_brd else []
            ok = has_brd and not errors
            line = ("no checklist to check" if not has_brd else "0 errors" if ok
                    else f"{len(errors)} error{'s' if len(errors) > 1 else ''}, first: {errors[0]}")  # fmt: skip
        out.append({"key": key, "name": name, "ok": ok, "line": line, "fix": "" if ok else fix})
    return out


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

    got = read_all(repo)
    cycles, cards = got if got is not None else ([], [])
    board = view(cards, cycles) if got is not None else None
    titles = {y.id: y for y in cycles}
    closed = {y.id for y in cycles if y.state == "closed"}
    live = [c for c in cards if c.cycle not in closed]

    def where(c) -> str:
        y = titles.get(c.cycle)
        if y is None:
            return "Backlog"
        mine = [x for x in cards if x.cycle == y.id]
        return f"{y.id} {sum(1 for x in mine if x.state == 'done')}/{len(mine)}"

    working_on = [{"title": f"{c.id} · {c.title}", "source": "board", "path": PATH, "card": c.id, "cycle": c.cycle,
                   "sentence": f"doing · {lane(c.files)} · {where(c)}"}
                  for c in live if c.state == "doing"]  # fmt: skip
    needs_you = [{"source": "board", "id": c.id, "kind": "card", "text": c.note or c.title, "state": "open",
                  "cycle": c.cycle}
                 for c in live if c.state == "needs you"]  # fmt: skip
    doc = {"repo": str(repo), "checklist": checklist, "board": board, "needs_you": needs_you,
           "working_on": working_on, "changes": _git_log(repo, changes)}  # fmt: skip
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
        out.append("Board: " + (", ".join(f"{len(ids)} {s}" for s, ids in b["columns"].items() if ids) or "empty"))
        shut = {y["id"] for y in b["cycles"] if y["state"] == "closed"}
        for y in b["cycles"]:
            if y["state"] == "open":
                out.append(f"  cycle {y['id']} · {y['title']} · {y['done']}/{len(y['cards'])} done"
                           + (f" ({y['plan']})" if y["plan"] else ""))  # fmt: skip
        for card in b["cards"]:
            if card["state"] != "done" and card["cycle"] not in shut:
                waits = f"  (after {', '.join(card['waits'])})" if card["waits"] else ""
                at = card["cycle"] or "Backlog"
                out.append(f"  {card['id']:<4} {card['state']:<9} {at:<8} {card['lane']:<8} {card['title']}{waits}"[:220])
        if shut:
            out.append(f"  closed cycles: {', '.join(sorted(shut))}")
    if doc["working_on"]:
        out.append("Working on:")
        for w in doc["working_on"]:
            out.append(f"  {w['title']} — {w['sentence']}")
    needs = doc["needs_you"]
    out.append(f"Needs you: {len(needs)}" if needs else "Needs you: nothing")
    for q in needs:
        out.append(f"  {q['id']}  {q['kind']}  {q['text']}"[:220])
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
