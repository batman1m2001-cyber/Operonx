"""Datasets: a JSONL file of cases, one per line.

``input`` is the item the graph receives; ``expected`` is optional. A line
without ``input`` is itself the input. ``"dataset:name"`` names
``<project>/datasets/name.jsonl``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, Iterable, List, Union

__all__ = ["CASE_KEYS", "Dataset", "case_id", "dataset_path"]

#: Row keys that describe a case rather than being its input.
CASE_KEYS = ("id", "input", "expected", "tags", "from", "note")


# ── datasets ─────────────────────────────────────────────────────────────


def dataset_path(ref: Union[str, Path], root: Union[str, Path, None] = None) -> Path:
    """``dataset:name`` → ``<root>/datasets/name.jsonl``; a path stays a path
    (relative to *root*)."""
    root = Path(root) if root is not None else Path.cwd()
    text = str(ref)
    if text.startswith("dataset:"):
        return root / "datasets" / f"{text.partition(':')[2]}.jsonl"
    path = Path(text)
    return path if path.is_absolute() else root / path


def case_id(row: Mapping) -> str:
    """A row's id: its own, else a hash of its input (stable across adds)."""
    if row.get("id") not in (None, ""):
        return str(row["id"])
    body = json.dumps(row.get("input", row), sort_keys=True, default=str)
    return hashlib.sha1(body.encode()).hexdigest()[:12]


class Dataset:
    """A JSONL file of cases. Reading is lazy; adding appends and dedupes."""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)

    @property
    def name(self) -> str:
        return self.path.stem

    def rows(self) -> List[Dict[str, Any]]:
        if not self.path.is_file():
            return []
        out: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    raise ValueError(f"{self.path}:{n}: not JSON ({exc})") from None
                if not isinstance(row, dict) or "input" not in row:
                    row = {"input": row}
                row["id"] = case_id(row)
                out.append(row)
        return out

    def add(self, rows: Iterable[Mapping]) -> List[str]:
        """Append *rows* whose id is not already there; returns the ids added."""
        have = {r["id"] for r in self.rows()}
        added: List[str] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for row in rows:
                row = dict(row)
                if "input" not in row:
                    raise ValueError("a case needs an `input`")
                row["id"] = case_id(row)
                if row["id"] in have:
                    continue
                have.add(row["id"])
                ordered = {k: row[k] for k in CASE_KEYS if k in row}
                ordered.update({k: v for k, v in row.items() if k not in ordered})
                fh.write(json.dumps(ordered, ensure_ascii=False, default=str) + "\n")
                added.append(row["id"])
        return added

    def __len__(self) -> int:
        return len(self.rows())

    def __repr__(self) -> str:
        return f"Dataset({str(self.path)!r})"
