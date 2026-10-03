"""Datasets: a JSONL file of cases, one per line.

``input`` is the item the graph receives; ``expected`` is optional. A line
without ``input`` is itself the input. ``"dataset:name"`` names
``<project>/datasets/name.jsonl``. A case may also carry ``tags``, a
``split`` (``dev``, ``test``…), a ``cluster`` and a reference
``trajectory``; :meth:`Dataset.select` picks cases by them,
:meth:`Dataset.problems` says what is malformed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

__all__ = ["CASE_KEYS", "Dataset", "case_id", "dataset_path", "diff_rows", "parse_rows"]

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
    """A JSONL file of cases. Reading is lazy; adding appends and dedupes.

    :meth:`select` gives a view of some of the cases (a split, tags, ids,
    a stable sample) — the same file, read through the selection.
    """

    def __init__(
        self,
        path: Union[str, Path],
        *,
        split: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
        ids: Optional[Sequence[str]] = None,
        sample: Optional[int] = None,
    ):
        self.path = Path(path)
        if sample is not None and (isinstance(sample, bool) or int(sample) < 1):
            raise ValueError(f"Dataset: sample is a number of cases, ≥ 1, not {sample!r}")
        self.split = split
        self.tags = tuple(tags) if tags else None
        self.ids = tuple(str(i) for i in ids) if ids else None
        self.sample = int(sample) if sample is not None else None

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def selection(self) -> Dict[str, Any]:
        """What :meth:`select` chose, as data (``{}`` for every case)."""
        out: Dict[str, Any] = {}
        if self.split is not None:
            out["split"] = self.split
        if self.tags:
            out["tags"] = list(self.tags)
        if self.ids:
            out["ids"] = list(self.ids)
        if self.sample is not None:
            out["sample"] = self.sample
        return out

    def select(
        self,
        *,
        split: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
        ids: Optional[Sequence[str]] = None,
        sample: Optional[int] = None,
    ) -> "Dataset":
        """The cases of *split*, carrying any of *tags*, among *ids* (each
        must exist), then the *sample* of them with the smallest
        ``sha256(id)`` — the same N on every machine and every run. A
        criterion given replaces this view's own; one not given is kept."""
        return Dataset(
            self.path,
            split=split if split is not None else self.split,
            tags=tags if tags else self.tags,
            ids=ids if ids else self.ids,
            sample=sample if sample is not None else self.sample,
        )

    def all_rows(self) -> List[Dict[str, Any]]:
        """Every case in the file, whatever the selection."""
        if not self.path.is_file():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            return parse_rows(fh, where=str(self.path))

    def rows(self) -> List[Dict[str, Any]]:
        """The selected cases, in file order."""
        rows = self.all_rows()
        if not self.selection:
            return rows
        if self.split is not None:
            rows = [r for r in rows if r.get("split") == self.split]
        if self.tags:
            wanted = set(self.tags)
            rows = [r for r in rows if wanted & set(r.get("tags") or ())]
        if self.ids:
            have = {r["id"] for r in rows}
            missing = [i for i in self.ids if i not in have]
            if missing:
                raise ValueError(f"cases not in {self.path} (or not selected): {missing}")
            wanted = set(self.ids)
            rows = [r for r in rows if r["id"] in wanted]
        if self.sample is not None and len(rows) > self.sample:
            chosen = set(sorted((r["id"] for r in rows), key=_stable)[: self.sample])
            rows = [r for r in rows if r["id"] in chosen]
        return rows

    def problems(self) -> List[Tuple[int, str]]:
        """What is wrong with the file, each with its line (0: the file)."""
        if not self.path.is_file():
            return [(0, "no such file")]
        out: List[Tuple[int, str]] = []
        first: Dict[str, int] = {}
        with self.path.open("r", encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    out.append((n, f"not JSON: {getattr(exc, 'msg', exc)}"))
                    continue
                if not isinstance(row, dict) or "input" not in row:
                    row = {"input": row}
                cid = case_id(row)
                if cid in first:
                    out.append((n, f"duplicate id {cid!r} (first on line {first[cid]})"))
                else:
                    first[cid] = n
                out.extend((n, why) for why in _row_problems(row))
        return out

    def add(self, rows: Iterable[Mapping]) -> List[str]:
        """Append *rows* whose id is not already there; returns the ids added."""
        have = {r["id"] for r in self.all_rows()}
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
        chosen = "".join(f", {k}={v!r}" for k, v in self.selection.items())
        return f"Dataset({str(self.path)!r}{chosen})"


def parse_rows(lines: Iterable[str], where: str = "<dataset>") -> List[Dict[str, Any]]:
    """JSONL lines as cases: a line without ``input`` is the input, and
    every case gets its id."""
    out: List[Dict[str, Any]] = []
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{where}:{n}: not JSON ({exc})") from None
        if not isinstance(row, dict) or "input" not in row:
            row = {"input": row}
        row["id"] = case_id(row)
        out.append(row)
    return out


def _stable(cid: str) -> str:
    return hashlib.sha256(cid.encode("utf-8")).hexdigest()


def _row_problems(row: Mapping) -> List[str]:
    out = []
    tags = row.get("tags")
    if tags is not None and not (isinstance(tags, list) and all(isinstance(x, str) for x in tags)):
        out.append(f"tags is a list of strings, not {tags!r}")
    for key in ("split", "cluster"):
        value = row.get(key)
        if value is not None and not isinstance(value, str):
            out.append(f"{key} is a string, not {value!r}")
    traj = row.get("trajectory")
    if traj is not None:
        if not isinstance(traj, dict):
            out.append(f"trajectory is {{ops: [...], tool_calls: [...]}}, not {traj!r}")
        else:
            for key in ("ops", "tool_calls"):
                if key in traj and not isinstance(traj[key], list):
                    out.append(f"trajectory.{key} is a list, not {traj[key]!r}")
    return out


def diff_rows(old: Iterable[Mapping], new: Iterable[Mapping]) -> Dict[str, List[str]]:
    """Two versions of a dataset, case by case: ids ``added``, ``removed``,
    and ``changed`` (what the case asks or expects — its ``case_hash``)."""
    from .fingerprint import case_hash

    before = {str(r["id"]): case_hash(r) for r in old}
    after = {str(r["id"]): case_hash(r) for r in new}
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(c for c in set(before) & set(after) if before[c] != after[c]),
    }
