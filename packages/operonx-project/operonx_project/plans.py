"""Plan docs: a Markdown file under ``docs/`` that starts by saying where it stands (PROJECT_MODE_PLAN D1).

Work done outside the engine (a Claude Code session, the studio's assistant) is planned in Markdown. A plan
says where it stands in YAML front matter, which GitHub renders as a table::

    ---
    title: Wealth names
    status: in_progress            # plan | in_progress | done | parked
    phases:
      - {id: W0, title: Names in the glossary, status: done}   # open | in_progress | done | skipped
      - {id: W1, title: The four groups, status: in_progress}
    rows: [OWN-WN-01]              # the checklist rows it serves (optional)
    updated: 2026-10-22
    ---

:func:`find` reads every plan under ``docs/`` (the engine's own ``docs/brd/board/`` is not plans), with what
is wrong in a block (an error) and the ``*_PLAN.md`` files that have none (a warning: a project adopts the
block at its own pace). A phase's status changes in the commit that finishes it, as a checklist row does.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

__all__ = ["PHASE_STATUSES", "PLAN_STATUSES", "Phase", "Plan", "PlanError", "find", "front_matter", "parse"]

PLAN_STATUSES = ("plan", "in_progress", "done", "parked")
PHASE_STATUSES = ("open", "in_progress", "done", "skipped")
_HEAD = 8192  # a block is at the top: a big doc is not read whole to find out it has none
_SKIP = ("brd/board/", "brd/sources/")  # the engine's board files; verbatim copies of the client's sources


class PlanError(ValueError):
    pass


@dataclass
class Phase:
    id: str
    title: str
    status: str


@dataclass
class Plan:
    path: str  # relative to the repo
    title: str
    status: str
    phases: List[Phase] = field(default_factory=list)
    rows: List[str] = field(default_factory=list)
    updated: str = ""

    @property
    def done(self) -> int:
        return sum(1 for p in self.phases if p.status in ("done", "skipped"))

    @property
    def current(self) -> Optional[Phase]:
        """The phase being worked: the first in progress, else the first still open."""
        return next((p for p in self.phases if p.status == "in_progress"),
                    next((p for p in self.phases if p.status == "open"), None))  # fmt: skip

    def as_dict(self) -> Dict[str, Any]:
        cur = self.current
        return {"path": self.path, "title": self.title, "status": self.status, "updated": self.updated,
                "rows": self.rows, "done": self.done, "total": len(self.phases),
                "current": cur.id if cur and self.status == "in_progress" else None,
                "phases": [{"id": p.id, "title": p.title, "status": p.status} for p in self.phases]}  # fmt: skip


def front_matter(text: str) -> Optional[Dict[str, Any]]:
    """The YAML block a document starts with, or None when it has none. A block that is not YAML raises."""
    if not text.startswith("---"):
        return None
    m = re.match(r"---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    if not m:
        raise PlanError("a block opened with --- is never closed")
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise PlanError(f"its block is not YAML: {str(exc).splitlines()[0]}") from None
    if not isinstance(data, dict):
        raise PlanError("its block is not a mapping (key: value lines)")
    return data


def _heading(text: str) -> str:
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    return m.group(1) if m else ""


def parse(rel: str, data: Dict[str, Any], text: str = "") -> Plan:
    """A plan from its block, checked: every status from the vocabulary, every phase with an id."""
    status = str(data.get("status", "")).strip()
    if status not in PLAN_STATUSES:
        raise PlanError(f"status {status!r} is not one of {', '.join(PLAN_STATUSES)}")
    phases: List[Phase] = []
    raw = data.get("phases") or []
    if not isinstance(raw, list):
        raise PlanError("phases is not a list")
    seen = set()
    for i, p in enumerate(raw, 1):
        if not isinstance(p, dict) or not str(p.get("id", "")).strip():
            raise PlanError(f"phase {i} has no id")
        pid, pst = str(p["id"]).strip(), str(p.get("status", "open")).strip()
        if pst not in PHASE_STATUSES:
            raise PlanError(f"phase {pid}: status {pst!r} is not one of {', '.join(PHASE_STATUSES)}")
        if pid in seen:
            raise PlanError(f"phase {pid} is listed twice")
        seen.add(pid)
        phases.append(Phase(pid, str(p.get("title") or "").strip(), pst))
    rows = data.get("rows") or []
    if not isinstance(rows, list) or not all(isinstance(r, str) for r in rows):
        raise PlanError("rows is not a list of checklist ids")
    updated = data.get("updated") or ""
    updated = updated.isoformat() if isinstance(updated, (_dt.date, _dt.datetime)) else str(updated)
    plan = Plan(rel, str(data.get("title") or _heading(text) or Path(rel).stem).strip(), status, phases,
                [r.strip() for r in rows], updated)  # fmt: skip
    if status == "done" and plan.current is not None:
        raise PlanError(f"it is done but phase {plan.current.id} is still {plan.current.status}")
    return plan


def find(repo: Path, docs: str = "docs") -> Tuple[List[Plan], List[str], List[str]]:
    """Every plan under *repo*/*docs*: ``(plans, errors, warnings)``, each message naming its file.

    A Markdown file is a plan when its block has a ``status``; a block without one (another tool's) is not
    this module's business. A ``*_PLAN.md`` with no block is a warning."""
    repo = Path(repo)
    plans: List[Plan] = []
    errors: List[str] = []
    warnings: List[str] = []
    base = repo / docs
    if not base.is_dir():
        return plans, errors, warnings
    for md in sorted(base.rglob("*.md")):
        rel = md.relative_to(repo).as_posix()
        if any(s in rel for s in _SKIP):
            continue
        with md.open(encoding="utf-8", errors="replace") as f:
            head = f.read(_HEAD)
            if head.startswith("---"):  # a block: read it whole, however long its phases run
                head += f.read()
        try:
            data = front_matter(head)
        except PlanError as exc:
            errors.append(f"{rel}: {exc}")
            continue
        if data is None or "status" not in data:
            if md.name.endswith("_PLAN.md") and data is None:
                warnings.append(f"{rel}: a plan with no status block (PROJECT_MODE_PLAN D1)")
            continue
        try:
            plans.append(parse(rel, data, head))
        except PlanError as exc:
            errors.append(f"{rel}: {exc}")
    return plans, errors, warnings
