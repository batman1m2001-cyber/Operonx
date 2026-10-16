"""``task_flow``: the wiring only; the ops are in ``ops.py``.

``round_`` loops on itself while its ``next`` is ``fix``, parks on an
``InterruptOp`` when it is ``ask`` (the answer comes back through ``keep``
into the ``answers`` cell and the next round reads it), and ends the run
otherwise. ``state`` and ``outcome`` are declared cells, so the journal
keeps them across rounds, parks and crashes.
"""

from __future__ import annotations

import operator

from operonx import END, PARENT, START, InterruptOp, graph
from operonx.core.ops import if_

from operonx_project.flows.task_flow.ops import keep, prepare, round_


@graph
def task_flow(key, mid, cid, reopen):
    PARENT.declare(state={}, answers=[], outcome=None, reducers={"answers": operator.add})
    p = prepare(key=key, mid=mid, cid=cid, reopen=reopen)
    p["state"] >> PARENT["state"]
    r = round_(key=key, mid=mid, cid=cid, state=PARENT["state"], answers=PARENT["answers"])
    r["state"] >> PARENT["state"]
    r["outcome"] >> PARENT["outcome"]
    ask = InterruptOp(payload=r["question"])
    k = keep(response=ask["response"])
    k["answers"] >> PARENT["answers"]
    START >> p >> r >> if_(r["next"] == "ask", ask).if_(r["next"] == "fix", r).else_(END)
    ask >> k >> r
