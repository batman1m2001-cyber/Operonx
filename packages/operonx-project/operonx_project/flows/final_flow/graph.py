"""``final_flow``: the wiring only; the ops are in ``ops.py``.

full checks → held-out tests → one review of the whole milestone →
requirement rows (each with the tests that prove it, committed) → the
BRD check → the verdict: the gaps, each naming the card it re-opens.
"""

from __future__ import annotations

from operonx import END, START, graph

from operonx_project.flows.final_flow.ops import (
    brd_checked,
    full_checks,
    heldout_all,
    record_rows,
    verdict,
    whole_review,
)


@graph
def final_flow(key, mid):
    c = full_checks(key=key, mid=mid)
    h = heldout_all(key=key, mid=mid)
    v = whole_review(key=key, mid=mid)
    r = record_rows(key=key, mid=mid)
    b = brd_checked(key=key, mid=mid)
    fin = verdict(key=key, mid=mid, checks=c["gaps"], heldout=h["gaps"], review=v["gaps"], rows=r["gaps"],
                  brd=b["gaps"], verified=r["verified"])  # fmt: skip
    START >> c >> h >> v >> r >> b >> fin >> END
