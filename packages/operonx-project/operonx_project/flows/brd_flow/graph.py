"""``brd_flow``: the wiring only; the ops are in ``ops.py``.

read → extract (one session, checked, sent back) → hunt (a second session,
for misses) → write (records, questions, matrix rows). ``only`` limits the
run to some sections: the changed ones of a new source version.
"""

from __future__ import annotations

from operonx import END, START, graph

from operonx_project.flows.brd_flow.ops import extract, hunt, read, write


@graph
def brd_flow(key, brd, source, sid, prefix, name, title, only):
    r = read(key=key, brd=brd, source=source, sid=sid, name=name, only=only)
    e = extract(key=key, sid=sid, prefix=prefix, sections=r["sections"], previous=r["previous"],
                figures=r["figures"])  # fmt: skip
    h = hunt(key=key, sid=sid, prefix=prefix, sections=r["sections"], records=e["records"], figures=r["figures"])
    w = write(key=key, brd=brd, name=name, title=title, sid=sid, records=h["records"],
              descriptive=e["descriptive"], sections=r["sections"], problems=e["problems"],
              rejected=e["rejected"], found=h["found"], hunt_rejected=h["rejected"],
              extract_cost=e["cost_usd"], hunt_cost=h["cost_usd"])  # fmt: skip
    START >> r >> e >> h >> w >> END
