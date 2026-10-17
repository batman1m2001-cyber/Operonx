"""The BRD folder: made from the template, its matrix summary kept true.

``init_brd`` writes OneWealth's layout, generalized, into ``<repo>/docs/brd``
(``operonx-project brd init``). It only adds what is missing: an existing
file is never overwritten.

``refresh_summary`` recomputes the matrix's summary table from its rows, so
the checker's "summary equals the rows" holds after every write.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections import Counter
from pathlib import Path
from typing import List, Optional

from operonx_project.brd.checker import BrdConfig, _groups, defined_requirements, matrix_rows, row_status
from operonx_project.brd.records import STATUSES

__all__ = ["TEMPLATE", "init_brd", "refresh_summary"]

TEMPLATE = Path(__file__).parent / "template"


def init_brd(repo: Path, path: str = "docs/brd", project: Optional[str] = None) -> List[Path]:
    """Write the template's files that *repo*/*path* lacks; returns them."""
    dest = Path(repo) / path
    name = project or Path(repo).resolve().name
    today = _dt.date.today().isoformat()
    written: List[Path] = []
    for src in sorted(TEMPLATE.rglob("*")):
        if not src.is_file():
            continue
        target = dest / src.relative_to(TEMPLATE)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix in (".md", ".toml"):
            target.write_text(src.read_text(encoding="utf-8").replace("{project}", name).replace("{date}", today),
                              encoding="utf-8")  # fmt: skip
        else:
            target.write_bytes(src.read_bytes())
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
