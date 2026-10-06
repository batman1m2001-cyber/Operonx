"""XLSX with the stdlib (``zipfile`` + ``defusedxml``).

Each visible sheet becomes a level-1 heading (its name) followed by the tables
found in it. Table discovery is docling's (``msexcel_backend.py``): every cell
with a value (or inside a merged range) seeds a flood fill over its 4-neighbours;
a connected component's bounding rectangle is one table, interior gaps become
empty cells, and a merged range's shadow cells are empty. Rules kept from
docling: a first row holding a single text cell over a wider table, above a row
of at least two cells, is a section label (a paragraph), not a header; a
one-cell table is a paragraph.

Cell values are the stored values: numbers as written in the file, dates as
Excel serial numbers (number formats are not applied — recorded as a known
limitation in PLAN.md).
"""

from __future__ import annotations

import re
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from operonx_kb.errors import DocumentParseError
from operonx_kb.parsing._ooxml import NS, Package, q
from operonx_kb.parsing.base import ParsedDoc, Parser, RawBlock

__all__ = ["XlsxParser", "cell_ref", "parse_ref"]

_R = NS["r"]
_REF = re.compile(r"^([A-Z]+)(\d+)$")

Cell = Tuple[int, int]  # (row, col), 0-based


def parse_ref(ref: str) -> Cell:
    """``"B7"`` → ``(6, 1)``."""
    m = _REF.match(ref.upper())
    if not m:
        raise DocumentParseError(f"bad cell reference {ref!r} in XLSX")
    col = 0
    for ch in m.group(1):
        col = col * 26 + ord(ch) - 64
    return int(m.group(2)) - 1, col - 1


def cell_ref(row: int, col: int) -> str:
    """``(6, 1)`` → ``"B7"``."""
    letters = ""
    col += 1
    while col:
        col, r = divmod(col - 1, 26)
        letters = chr(65 + r) + letters
    return f"{letters}{row + 1}"


def _shared_strings(pkg: Package) -> List[str]:
    root = pkg.xml("xl/sharedStrings.xml")
    if root is None:
        return []
    return ["".join(t.text or "" for t in si.iter(q("s:t"))) for si in root.findall(q("s:si"))]


def _sheet_cells(root, strings: List[str]) -> Tuple[Dict[Cell, str], List[Tuple[Cell, Cell]]]:
    values: Dict[Cell, str] = {}
    data = root.find(q("s:sheetData"))
    for c in data.iter(q("s:c")) if data is not None else []:
        ref = c.get("r")
        if not ref:
            continue
        kind = c.get("t", "n")
        if kind == "inlineStr":
            text = "".join(t.text or "" for t in c.iter(q("s:t")))
        else:
            v = c.find(q("s:v"))
            raw = v.text if v is not None and v.text is not None else ""
            if kind == "s" and raw:
                idx = int(raw)
                text = strings[idx] if 0 <= idx < len(strings) else ""
            elif kind == "b":
                text = "TRUE" if raw == "1" else "FALSE" if raw else ""
            else:
                text = raw
        if text.strip():
            values[parse_ref(ref)] = text
    merges: List[Tuple[Cell, Cell]] = []
    mc = root.find(q("s:mergeCells"))
    for m in mc.findall(q("s:mergeCell")) if mc is not None else []:
        a, _, b = (m.get("ref") or "").partition(":")
        if a and b:
            merges.append((parse_ref(a), parse_ref(b)))
    return values, merges


def _tables(values: Dict[Cell, str], merges: List[Tuple[Cell, Cell]]) -> List[Tuple[Cell, Cell]]:
    """Bounding rectangles of the connected regions of non-empty cells."""
    filled: Set[Cell] = set(values)
    for (r0, c0), (r1, c1) in merges:
        if (r0, c0) in values:
            filled.update((r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1))
    seen: Set[Cell] = set()
    rects: List[Tuple[Cell, Cell]] = []
    for start in sorted(filled):
        if start in seen:
            continue
        queue = deque([start])
        seen.add(start)
        r0 = r1 = start[0]
        c0 = c1 = start[1]
        while queue:
            r, c = queue.popleft()
            r0, r1, c0, c1 = min(r0, r), max(r1, r), min(c0, c), max(c1, c)
            for nxt in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                if nxt in filled and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        # The rectangle swallows cells of other components inside it.
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                seen.add((r, c))
        rects.append(((r0, c0), (r1, c1)))
    return sorted(rects)


class XlsxParser(Parser):
    """Excel workbooks (``.xlsx``, ``.xlsm``)."""

    name = "xlsx"
    version = "1"
    mimes = frozenset(
        {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel.sheet.macroEnabled.12",
        }
    )
    extensions = frozenset({".xlsx", ".xlsm"})

    def parse(self, data: bytes, *, name: Optional[str] = None) -> ParsedDoc:
        pkg = Package(data, "XLSX")
        book = pkg.xml("xl/workbook.xml")
        if book is None:
            raise DocumentParseError("XLSX has no xl/workbook.xml; is it an Excel workbook?")
        rels = pkg.rels("xl/workbook.xml")
        strings = _shared_strings(pkg)
        blocks: List[RawBlock] = []
        sheets = book.find(q("s:sheets"))
        for sheet in sheets if sheets is not None else []:
            if sheet.get("state") in ("hidden", "veryHidden"):
                continue
            part = rels.get(sheet.get(f"{{{_R}}}id") or "")
            root = pkg.xml(part) if part else None
            if root is None:
                continue
            sheet_name = sheet.get("name") or ""
            values, merges = _sheet_cells(root, strings)
            if not values:
                continue
            blocks.append(
                RawBlock(kind="heading", level=1, text=sheet_name, attrs={"sheet": sheet_name})
            )
            shadows = {
                (r, c)
                for (r0, c0), (r1, c1) in merges
                for r in range(r0, r1 + 1)
                for c in range(c0, c1 + 1)
                if (r, c) != (r0, c0)
            }
            spans = {(r0, c0): (c1 - c0 + 1) for (r0, c0), (r1, c1) in merges}
            for (r0, c0), (r1, c1) in _tables(values, merges):
                rows = [
                    ["" if (r, c) in shadows else values.get((r, c), "") for c in range(c0, c1 + 1)]
                    for r in range(r0, r1 + 1)
                ]
                anchor = {"sheet": sheet_name, "range": f"{cell_ref(r0, c0)}:{cell_ref(r1, c1)}"}
                if len(rows) == 1 and len(rows[0]) == 1:
                    blocks.append(RawBlock(kind="paragraph", text=rows[0][0], attrs=anchor))
                    continue
                first = [v for v in rows[0] if v]
                if (
                    len(rows) > 2
                    and len(first) == 1
                    and rows[0][0]
                    and spans.get((r0, c0), 1) > 1
                    and sum(1 for v in rows[1] if v) >= 2
                ):
                    blocks.append(RawBlock(kind="paragraph", text=rows[0][0], attrs=anchor))
                    rows = rows[1:]
                blocks.append(
                    RawBlock(kind="table", attrs={"rows": rows, "header_rows": 1, **anchor})
                )
        return ParsedDoc(blocks=blocks, parser=self.name)
