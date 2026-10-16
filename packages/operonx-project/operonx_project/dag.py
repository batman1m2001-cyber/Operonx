"""The cards' dependency graph: validation, waves, what is downstream, path overlap.

Waves are for display only (a card's depth in the DAG); the scheduler starts
a card as soon as its own dependencies are done, not when its wave is.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Set

from operonx_project.model import Card

__all__ = ["DagError", "downstream", "paths_overlap", "topo_order", "validate", "waves"]


class DagError(ValueError):
    """The plan's cards do not form a valid DAG; ``problems`` lists every reason."""

    def __init__(self, problems: List[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def validate(cards: Sequence[Card], max_cards: int = 0) -> None:
    """Raise :class:`DagError` naming every problem: duplicate ids, unknown or
    self dependencies, cycles (each given as its path), too many cards."""
    problems: List[str] = []
    ids = [c.id for c in cards]
    seen: Set[str] = set()
    for cid in ids:
        if cid in seen:
            problems.append(f"duplicate card id {cid!r}")
        seen.add(cid)
    for c in cards:
        for d in c.deps:
            if d == c.id:
                problems.append(f"{c.id} depends on itself")
            elif d not in seen:
                problems.append(f"{c.id} depends on unknown card {d!r}")
    if max_cards and len(cards) > max_cards:
        problems.append(f"{len(cards)} cards is more than max_cards={max_cards}")
    cycle = _find_cycle({c.id: [d for d in c.deps if d in seen and d != c.id] for c in cards})
    if cycle:
        problems.append("cycle: " + " → ".join(cycle))
    if problems:
        raise DagError(problems)


def _find_cycle(deps: Dict[str, List[str]]) -> List[str]:
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in deps}
    stack: List[str] = []

    def visit(n: str) -> List[str]:
        color[n] = GREY
        stack.append(n)
        for m in deps.get(n, []):
            if color.get(m) == GREY:
                return stack[stack.index(m) :] + [m]
            if color.get(m) == WHITE:
                found = visit(m)
                if found:
                    return found
        stack.pop()
        color[n] = BLACK
        return []

    for n in sorted(deps):
        if color[n] == WHITE:
            found = visit(n)
            if found:
                return found
    return []


def topo_order(cards: Sequence[Card]) -> List[str]:
    """Ids with every card after its dependencies (ties by priority, then id)."""
    by_id = {c.id: c for c in cards}
    done: Set[str] = set()
    out: List[str] = []
    pending = sorted(by_id, key=lambda i: (by_id[i].priority, i))
    while pending:
        progressed = False
        for cid in list(pending):
            if all(d in done or d not in by_id for d in by_id[cid].deps):
                out.append(cid)
                done.add(cid)
                pending.remove(cid)
                progressed = True
        if not progressed:
            raise DagError(["cycle among " + ", ".join(pending)])
    return out


def waves(cards: Sequence[Card]) -> Dict[str, int]:
    """Each card's wave: 1 for no dependencies, else 1 + its deepest dependency's."""
    by_id = {c.id: c for c in cards}
    wave: Dict[str, int] = {}
    for cid in topo_order(cards):
        deps = [d for d in by_id[cid].deps if d in by_id]
        wave[cid] = 1 + max((wave[d] for d in deps), default=0)
    return wave


def downstream(cards: Sequence[Card], cid: str) -> Set[str]:
    """Every card that depends on *cid*, directly or not."""
    children: Dict[str, List[str]] = {}
    for c in cards:
        for d in c.deps:
            children.setdefault(d, []).append(c.id)
    out: Set[str] = set()
    todo = list(children.get(cid, []))
    while todo:
        n = todo.pop()
        if n not in out:
            out.add(n)
            todo.extend(children.get(n, []))
    return out


_WILD = set("*?[")


def _prefix(glob: str) -> List[str]:
    """The literal path segments before the first wildcard."""
    parts: List[str] = []
    for seg in glob.strip("/").split("/"):
        if not seg or seg == ".":
            continue
        if _WILD & set(seg):
            return parts + ["*"]
        parts.append(seg)
    return parts


def _could_meet(a: str, b: str) -> bool:
    pa, pb = _prefix(a), _prefix(b)
    for x, y in zip(pa, pb):
        if x == "*" or y == "*":
            return True
        if x != y:
            return False
    # equal, or one is a literal prefix of the other: a directory and a file in it
    return True


def paths_overlap(a: Iterable[str], b: Iterable[str]) -> bool:
    """Whether two cards' path globs could touch a common file.

    Conservative: it compares the literal segments before each glob's first
    wildcard, so ``src/a/**`` and ``src/a/x.py`` overlap, ``src/a/*`` and
    ``src/b/*`` do not, and ``**/*.py`` overlaps everything. A card with no
    paths declared is treated as touching everything.
    """
    a, b = list(a), list(b)
    if not a or not b:
        return True
    return any(_could_meet(x, y) for x in a for y in b)
