"""The BRD folder: made from the template, its matrix summary kept true.

``init_brd`` writes OneWealth's layout, generalized, into ``<repo>/docs/brd``
(``operonx-project brd init``). The template is operonx's own
(``operonx.cli.init.brd_files``, what ``operonx init`` writes), so a project
made either way has the same folder. It only adds what is missing: an
existing file is never overwritten.

``refresh_summary`` recomputes the matrix's summary table from its rows, so
the checker's "summary equals the rows" holds after every write.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import List, Optional

from operonx.cli.init import brd_files

from operonx_project.brd.checker import BrdConfig, _groups, defined_requirements, matrix_rows, row_status
from operonx_project.brd.records import STATUSES

__all__ = ["init_brd", "refresh_summary"]


def init_brd(repo: Path, path: str = "docs/brd", project: Optional[str] = None) -> List[Path]:
    """Write the template's files that *repo*/*path* lacks; returns them."""
    dest = Path(repo) / path
    written: List[Path] = []
    for rel, content in brd_files(project or Path(repo).resolve().name).items():
        target = dest / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        written.append(target)
    return written


_SUMMARY_HEAD = "| Set | Rows | " + " | ".join(STATUSES) + " |"
_SUMMARY_SEP = "|" + "---|" * (2 + len(STATUSES))


def refresh_summary(brd: Path) -> None:
    """Rewrite the ``## 0. Summary`` table from the matrix's rows (made when missing)."""
    cfg = BrdConfig.load(brd)
    if not cfg.matrix.exists():
        return
    rows = matrix_rows(cfg)
    sets = _groups(cfg, defined_requirements(cfg) | set(rows))
    counts = {name: Counter() for name, _ in sets}
    for rid, row in rows.items():
        status = row_status(row)
        for name, pattern in sets:
            if status and re.match(pattern, rid):
                counts[name][status] += 1
    table = [_SUMMARY_HEAD, _SUMMARY_SEP] + [
        f"| {name} | {sum(c.values())} | " + " | ".join(str(c[s]) for s in STATUSES) + " |"
        for name, c in counts.items()
    ]
    lines = cfg.matrix.read_text(encoding="utf-8").rstrip("\n").splitlines()
    if "## 0. Summary" in lines:
        i = lines.index("## 0. Summary") + 1
        while i < len(lines) and not lines[i].startswith("|"):
            i += 1
        j = i
        while j < len(lines) and lines[j].startswith("|"):
            j += 1
        lines[i:j] = table
    else:
        k = next((n for n, ln in enumerate(lines) if ln.startswith("## ")), len(lines))
        lines[k:k] = ["## 0. Summary", "", *table, ""]
    cfg.matrix.write_text("\n".join(lines) + "\n", encoding="utf-8")
