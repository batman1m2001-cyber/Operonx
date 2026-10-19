"""Run ``brd_flow`` on one source: the entry the CLI and the lifecycle use."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union

from operonx import Operon
from operonx.durable import SqliteJournal

from operonx_project.flows.brd_flow import BrdRuntime, brd_flow
from operonx_project.runtime import register

__all__ = ["extract_source"]


def _slug(text: str) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", text.lower())[:4]) or "source"


def _file_name(brd: Path, prefix: str, sid: str) -> str:
    """The source's requirements file: the prefix's, unless another source has
    it (a project's second source must never rewrite the first's file)."""
    base = _slug(prefix)
    f = brd / "requirements" / f"{base}_requirements.md"
    if not f.exists() or re.search(rf"^Extracted from {re.escape(sid)}\b", f.read_text(encoding="utf-8"), re.M):
        return base
    return f"{base}_{re.sub(r'[^a-z0-9]', '', sid.lower())}"


async def extract_source(
    rt: BrdRuntime,
    brd: Path,
    source: Path,
    sid: str,
    prefix: str,
    title: str,
    name: Optional[str] = None,
    only: Sequence[str] = (),
    journal: Optional[Union[str, Path]] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract *source* (registered as *sid*) into *brd*; returns ``write``'s report."""
    register(rt)
    engine = Operon(
        brd_flow,
        params={k: None for k in ("key", "brd", "source", "sid", "prefix", "name", "title", "only")},
        journal=SqliteJournal(str(journal or rt.data_dir / "brd_runs.db")),
        durability="sync",
    )
    inputs = {
        "key": rt.key,
        "brd": str(Path(brd)),
        "source": str(Path(source)),
        "sid": sid,
        "prefix": prefix,
        "name": name or _file_name(Path(brd), prefix, sid),
        "title": title,
        "only": list(only),
    }
    out = await engine.run(inputs, run_id=run_id)
    if out.get("$errors"):
        raise RuntimeError(f"brd_flow failed: {out['$errors']}")
    keys = ("file", "requirements", "matrix_rows", "questions", "descriptive", "problems", "rejected",
            "found_by_hunter", "cost_usd")  # fmt: skip
    return {k: out.get(k) for k in keys}
