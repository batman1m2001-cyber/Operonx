"""Rule-based reading order of a page's blocks, ported from docling.

This is docling's ``ReadingOrderPredictor._predict_page``
(``docling/models/postprocessing/reading_order_rb.py`` @ 51fe9ff) on our
top-left boxes, without its rtree (pages hold tens of blocks) and without the
separator elements:

1. **Left-to-right pairs.** Block *i* leads to block *j* on its right when
   *j* comes next in the input order, the two share most of their height
   (vertical IoU > 0.8) and nothing sits between them. Here both must also
   be short (``row_height``), or same-height paragraphs of two columns read
   across.
2. **Up/down links.** Block *i* is above *j* when it ends above *j*'s top
   and the two overlap horizontally, with no third block between them that
   overlaps either; a row's right-most member carries its links.
3. **Dilation.** A block in a one-to-one vertical chain is widened to its
   neighbour's extent (at most 15% of the page width) unless that makes it
   overlap another block (docling's code assigns the widened box even then;
   the overlap test is its stated intent), and the links are recomputed: a
   ragged short line still reaches the column it belongs to.
4. **Order.** Blocks with nothing above are heads, sorted top to bottom
   within a column and left to right across; each head is followed by a
   depth-first walk down the links, which first climbs to any unvisited
   block above. Columns come out one after the other.
"""

from __future__ import annotations

from functools import cmp_to_key
from typing import Dict, List, Sequence, Tuple

__all__ = ["reading_order"]

BBox = Tuple[float, float, float, float]

_HORIZONTAL_DILATION = 0.15  # of the page width
_VERTICAL_OVERLAP_IOU = 0.8
_QUERY_PADDING = 0.1
_NEAR_VERTICAL_OVERLAP = 0.0025  # of the page height
_LEFT_EDGE_ALIGNMENT = 0.01  # of the page width
_INTERRUPTION_PADDING = 1.0
_EPS = 1.0e-3


def _overlaps_h(a: BBox, b: BBox) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0])


def _overlaps(a: BBox, b: BBox) -> bool:
    return _overlaps_h(a, b) and not (a[3] <= b[1] or b[3] <= a[1])


def _above(a: BBox, b: BBox) -> bool:
    """``a`` ends above ``b``'s top (docling's is_strictly_above)."""
    return a[3] < b[1] + _EPS


def _left_of(a: BBox, b: BBox) -> bool:
    return a[2] + _EPS < b[0]


def _v_iou(a: BBox, b: BBox) -> float:
    inter = min(a[3], b[3]) - max(a[1], b[1])
    if inter <= 0:
        return 0.0
    return inter / max(max(a[3], b[3]) - min(a[1], b[1]), 1e-9)


def _before(a: BBox, b: BBox) -> int:
    """docling's PageElement.__lt__: top first within a column, else left first."""
    if _overlaps_h(a, b):
        return -1 if a[3] < b[3] else (1 if b[3] < a[3] else 0)
    return -1 if a[0] < b[0] else (1 if b[0] < a[0] else 0)


def reading_order(
    boxes: Sequence[BBox],
    page_width: float,
    page_height: float,
    graphic: Sequence[bool] = (),
    row_height: float = float("inf"),
) -> List[int]:
    """Indices of ``boxes`` in reading order.

    ``boxes`` are top-left ``(x0, y0, x1, y1)``, given in their natural
    (top-to-bottom, left-to-right) order; ``graphic`` marks tables and figures,
    which docling leaves out of the near-overlap repair. Only boxes at most
    ``row_height`` tall pair up left to right: two paragraphs of the same
    height side by side are columns, not a row (docling pairs any two whose
    heights overlap by 0.8, and reads such columns across).
    """
    n = len(boxes)
    graphic = list(graphic) or [False] * n
    l2r: Dict[int, int] = {}
    r2l: Dict[int, int] = {}
    for i in range(n - 1):
        j = i + 1
        a, b = boxes[i], boxes[j]
        if (
            _left_of(a, b)
            and max(a[3] - a[1], b[3] - b[1]) <= row_height
            and _v_iou(a, b) > _VERTICAL_OVERLAP_IOU
            and not any(
                k not in (i, j)
                and a[2] < boxes[k][2]
                and boxes[k][0] < b[0]
                and max(a[1], b[1]) < boxes[k][3]
                and boxes[k][1] < min(a[3], b[3])
                for k in range(n)
            )
        ):
            l2r[i] = j
            r2l[j] = i

    def links(elems: Sequence[BBox]) -> Tuple[Dict[int, List[int]], Dict[int, List[int]]]:
        up: Dict[int, List[int]] = {i: [] for i in range(n)}
        dn: Dict[int, List[int]] = {i: [] for i in range(n)}
        for j, bj in enumerate(elems):
            if j in r2l:
                left = r2l[j]
                if j not in dn[left]:
                    dn[left].append(j)
                if left not in up[j]:
                    up[j].append(left)
            for i, bi in enumerate(elems):
                if i == j or not (_above(bi, bj) and _overlaps_h(bi, bj)):
                    continue
                if bi[2] + _QUERY_PADDING < bj[0] or bj[2] + _QUERY_PADDING < bi[0]:
                    continue
                if _interrupted(elems, i, j):
                    continue
                k = i
                while k in l2r:
                    k = l2r[k]
                dn[k].append(j)
                up[j].append(k)
        # Consecutive text boxes in a column can overlap slightly at their edges;
        # keep their input sequence when the strict-above test misses the link.
        tol = _NEAR_VERTICAL_OVERLAP * page_height
        for i in range(n - 1):
            j = i + 1
            if graphic[i] or graphic[j]:
                continue
            a, b = elems[i], elems[j]
            if (
                abs(a[0] - b[0]) < _LEFT_EDGE_ALIGNMENT * page_width
                and a[1] < b[1] <= a[3] + tol
                and a[3] < b[3]
                and a[3] > b[1]
                and j not in dn[i]
            ):
                dn[i].append(j)
                up[j].append(i)
        return up, dn

    up, dn = links(boxes)
    dilated = _dilate(boxes, up, dn, _HORIZONTAL_DILATION * page_width)
    up, dn = links(dilated)

    order_key = cmp_to_key(lambda i, j: _before(boxes[i], boxes[j]))
    heads = sorted((i for i in range(n) if not up[i]), key=order_key)
    for m in (up, dn):
        for i in m:
            m[i] = sorted(m[i], key=order_key)

    visited = [False] * n
    order: List[int] = []

    def climb(j: int) -> int:
        k, walked = j, {j}
        while True:
            for i in up[k]:
                if not visited[i] and i not in walked:
                    k = i
                    walked.add(i)
                    break
            else:
                return k

    for h in heads:
        if visited[h]:
            continue
        order.append(h)
        visited[h] = True
        stack: List[Tuple[List[int], int]] = [(dn[h], 0)]
        while stack:
            inds, offset = stack[-1]
            for pos in range(offset, len(inds)):
                k = climb(inds[pos])
                if not visited[k]:
                    order.append(k)
                    visited[k] = True
                    stack[-1] = (inds, pos + 1)
                    stack.append((dn[k], 0))
                    break
            else:
                stack.pop()
    # docling logs an error when the walk misses a block; keep every block.
    order.extend(i for i in range(n) if not visited[i])
    return order


def _interrupted(elems: Sequence[BBox], i: int, j: int) -> bool:
    """Whether a third block sits between ``i`` (above) and ``j`` in their lane."""
    a, b = elems[i], elems[j]
    x0 = min(a[0], b[0]) - _INTERRUPTION_PADDING
    x1 = max(a[2], b[2]) + _INTERRUPTION_PADDING
    for w, c in enumerate(elems):
        if w in (i, j) or c[2] < x0 or c[0] > x1:
            continue
        if (_overlaps_h(a, c) or _overlaps_h(b, c)) and _above(a, c) and _above(c, b):
            return True
    return False


def _dilate(
    boxes: Sequence[BBox], up: Dict[int, List[int]], dn: Dict[int, List[int]], limit: float
) -> List[BBox]:
    def one_to_one(upper: int, lower: int) -> bool:
        return dn.get(upper) == [lower] and up.get(lower) == [upper]

    out = list(boxes)
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        too_far = False
        for nb, ok in (
            (up[i][0] if up[i] else None, bool(up[i]) and one_to_one(up[i][0], i)),
            (dn[i][0] if dn[i] else None, bool(dn[i]) and one_to_one(i, dn[i][0])),
        ):
            if nb is None or not ok:
                continue
            d0, d1 = min(x0, boxes[nb][0]), max(x1, boxes[nb][2])
            if x0 - d0 > limit or d1 - x1 > limit:
                too_far = True  # docling leaves such a block as it is
                break
            x0, x1 = d0, d1
        grown = (x0, y0, x1, y1)
        if not too_far and not any(_overlaps(boxes[j], grown) for j in range(len(boxes)) if j != i):
            out[i] = grown
    return out
