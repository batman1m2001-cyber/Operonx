"""Linked milestones across repos (plan D17).

A milestone may need another project's milestone (``needs =
["operonx-studio#m3"]``; a bare ``m2`` is this project's). Started, it waits
in ``waiting_dependency`` until each one it needs is delivered (``done``);
the daemon looks again every few seconds and starts it then.

The other projects are found by name in ``~/.operonx/projects.json``, which
every :meth:`Project.open` keeps current: ``{name: its state folder}``. A
project not opened on this machine yet cannot be found, and says so.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

__all__ = ["index_path", "remember", "parse_need", "need_state", "unmet"]

_NEED = re.compile(r"^(?:([A-Za-z0-9_.-]+)#)?(m\d+)$")


def index_path() -> Path:
    return Path(os.environ.get("OPERONX_PROJECTS_INDEX") or Path.home() / ".operonx" / "projects.json")


def _index() -> Dict[str, str]:
    try:
        got = json.loads(index_path().read_text(encoding="utf-8"))
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(name: str, state: Path) -> None:
    """Note where project *name* keeps its state; never raises (an index is a convenience)."""
    try:
        idx = _index()
        if idx.get(name) == str(state):
            return
        idx[name] = str(state)
        f = index_path()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(idx, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(f)
    except OSError:
        pass


def parse_need(text: str, here: str) -> Tuple[str, str]:
    """``"operonx-studio#m3"`` → ``("operonx-studio", "m3")``; ``"m2"`` → ``(here, "m2")``."""
    m = _NEED.match(str(text).strip())
    if not m:
        raise ValueError(f"a need is <project>#m<n> or m<n>, not {text!r}")
    return m.group(1) or here, m.group(2)


def need_state(need: str, here: str, here_state: Path) -> Optional[str]:
    """The needed milestone's state, or None when it cannot be found."""
    name, mid = parse_need(need, here)
    state = here_state if name == here else (Path(_index()[name]) if name in _index() else None)
    db = state / "project.db" if state else None
    if db is None or not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT state FROM milestones WHERE id=?", (mid,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def unmet(needs: List[str], here: str, here_state: Path) -> List[str]:
    """The needs not delivered yet, each as ``"<need> (<its state>)"``."""
    out = []
    for n in needs:
        s = need_state(n, here, here_state)
        if s != "done":
            out.append(f"{n} ({s or 'not found on this machine'})")
    return out
