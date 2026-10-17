"""``plan_flow``: the wiring only; the ops are in ``ops.py``.

draft → review, and back to draft while the review says ``revise`` and
revise rounds are left (``plan_reviews``, default 2). The plan, with what
the review still says, is then the milestone's ``plan`` record, for the
owner's approval. ``state`` is a declared cell: the journal keeps it across
rounds and crashes.
"""

from __future__ import annotations

from operonx import END, PARENT, START, graph
from operonx.core.ops import if_

from operonx_project.flows.plan_flow.ops import draft, review


@graph
def plan_flow(key, mid, notes):
    PARENT.declare(state={})
    d = draft(key=key, mid=mid, state=PARENT["state"], notes=notes)
    d["state"] >> PARENT["state"]
    v = review(key=key, mid=mid, state=PARENT["state"])
    v["state"] >> PARENT["state"]
    START >> d >> v >> if_(v["next"] == "revise", d).else_(END)
