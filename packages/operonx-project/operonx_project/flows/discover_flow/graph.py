"""``discover_flow``: the wiring only; the ops are in ``ops.py``.

survey (a read-only session writes the brief and lists the commands) →
baseline (the commands run on the untouched integration tip).
"""

from __future__ import annotations

from operonx import END, START, graph

from operonx_project.flows.discover_flow.ops import baseline, survey


@graph
def discover_flow(key, mid):
    s = survey(key=key, mid=mid)
    b = baseline(key=key, mid=mid, survey=s["survey"])
    START >> s >> b >> END
