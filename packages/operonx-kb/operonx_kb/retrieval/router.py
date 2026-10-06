"""The query router (track5 §9.8): which questions go to graph search.

Graph search lifts multi-hop questions a lot (K6: +0.17 Recall@10 on 2Wiki and
MuSiQue) and costs single-hop questions a little (up to −0.06 Recall@5 on
Vietnamese sets), so ``mode="auto"`` sends only *relation* questions to it: a
chain of roles ("the mother of the director of film X"), a possessive role
("Nephalion's father"), a described entity ("the person after whom X was
named"), a place chain ("what county was X born in") or a comparison ("born
later", "same country"). Everything else goes to the collection's default mode.

The rules were fitted on the K6 dev splits and are English; a question in
another language goes to the default. ``docs/bench/router.md`` has the numbers.
"""

from __future__ import annotations

import re
from typing import Optional

__all__ = ["relation_rule"]

_ROLE = (
    r"(?:director|composer|performer|creator|author|founder|father|mother|parent|spouse|wife"
    r"|husband|child|son|daughter|brother|sister|sibling|employer|owner|producer|cast member"
    r"|publisher|manufacturer|developer|record label|label|grandfather|grandmother|uncle|aunt"
    r"|successor|predecessor|headquarters|birthplace|alma mater|team|member|maker|artist"
    r"|singer|writer|screenwriter|player|leader|president|capital|country|county|city|state)"
)

#: (name, pattern): the first match names the rule in the trace.
RULES = [
    (
        "role chain",
        re.compile(
            rf"\b{_ROLE}s?\b(?: \w+){{0,3}}? of (?:the |a |film |song |album |\w+ )?.*\b{_ROLE}\b",
            re.I,
        ),
    ),
    (
        "role of a work",
        re.compile(
            rf"\b(?:the )?{_ROLE}s? of (?:the )?(?:film|song|album|movie|series|work|book|novel|magazine|band|group|company)\b",
            re.I,
        ),
    ),
    ("possessive role", re.compile(rf"\w['’]s (?:\w+ )?{_ROLE}\b", re.I)),
    (
        "place chain",
        re.compile(
            r"\b(?:what|which|in which|in what) (?:county|district|state|province|country|city|region|continent)\b.*\b(?:born|died|located|headquartered|based|founded)\b|\bplace of (?:birth|death)\b|\bbirthplace\b",
            re.I,
        ),
    ),
    (
        "described entity",
        re.compile(
            r"\b(?:after|from) whom\b|\bwhose \w+|\bthe (?:\w+ ){1,3}(?:that|who|which|whose) \w+",
            re.I,
        ),
    ),
    (
        "comparison",
        re.compile(
            r"\b(?:same (?:country|nationality|place)|both|born (?:first|later|earlier)|(?:came out|released|founded|died|born) (?:first|earlier|later)|older|younger|earlier|later)\b",
            re.I,
        ),
    ),
]


def relation_rule(query: str) -> Optional[str]:
    """The name of the first rule ``query`` matches, or ``None``: not a relation question."""
    for name, pattern in RULES:
        if pattern.search(query or ""):
            return name
    return None
