"""Golden snapshots of element trees.

A snapshot is the normalised shape of a version: every element's path, kind,
level, layer, span and text, pages, and bboxes rounded to 0.01 (track5 §15.1:
"exact match on structure, tolerance on bbox"). Snapshots are JSON so a diff
reads like a review.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from operonx_kb.structure.build import VersionTree

__all__ = ["tree_snapshot", "compare_or_update"]


def tree_snapshot(tree: VersionTree) -> Dict[str, Any]:
    """The snapshot of ``tree``; independent of the version id."""
    elements = []
    for e in tree.elements:
        row: Dict[str, Any] = {"path": e.path, "kind": e.kind}
        if e.level is not None:
            row["level"] = e.level
        if e.layer != "body":
            row["layer"] = e.layer
        row["span"] = list(e.span) if e.span is not None else None
        if e.kind not in ("document", "section", "list"):
            row["text"] = e.text
        pages = sorted({r.page_no for r in e.regions})
        if pages:
            row["pages"] = pages
            row["bbox"] = [round(v, 2) for v in e.regions[0].bbox]
        for key in ("ordered", "marker", "lang", "header_rows"):
            if key in e.attrs:
                row[key] = e.attrs[key]
        if "target" in e.attrs:
            row["caption_of"] = _path_of(tree, e.attrs["target"])
        elements.append(row)
    return {
        "title": tree.title,
        "pages": [[p.page_no, round(p.width, 1), round(p.height, 1)] for p in tree.pages],
        "canonical": tree.canonical,
        "elements": elements,
    }


def _path_of(tree: VersionTree, element_id: str) -> str:
    for e in tree.elements:
        if e.id == element_id:
            return e.path
    return "?"


def _dumps(snapshot: Dict[str, Any]) -> str:
    """JSON with one element per line, so a snapshot diff shows one changed element per line."""
    lines = ["{"]
    keys = list(snapshot)
    for i, key in enumerate(keys):
        comma = "," if i < len(keys) - 1 else ""
        value = snapshot[key]
        if key == "elements":
            rows = [json.dumps(e, ensure_ascii=False) for e in value]
            body = ",\n  ".join(rows)
            lines.append(f' "{key}": [\n  {body}\n ]{comma}')
        else:
            lines.append(f" {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def compare_or_update(snapshot: Dict[str, Any], path: Path, update: bool) -> None:
    """Assert ``snapshot`` equals the stored one at ``path``; write it instead when
    ``update`` is set.

    A missing snapshot is written and the assertion fails, so a new fixture is
    reviewed once rather than passing silently.
    """
    text = _dumps(snapshot)
    if update or not path.exists():
        missing = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        assert update or not missing, f"new snapshot written to {path}; review it and rerun"
        return
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored == json.loads(text), (
        f"element tree differs from {path}; if the change is intended, rerun with --update-golden "
        "and review the diff"
    )
