"""Reusable graph and tree algorithms for DAG analysis.

Used by graph validation, scheduling, and trace collection.
"""

from collections import defaultdict
from typing import Callable, Dict, List, Optional, Set, Tuple

# ── Graph algorithms ─────────────────────────────────────────────────────────


def topo_sort(
    nodes: List[str],
    nexts: Dict[str, List[str]],
    prevs: Dict[str, List[str]],
) -> List[str]:
    """Kahn's algorithm topological sort.

    Args:
        nodes: All node names.
        nexts: Forward adjacency — nexts[a] = [b, c] means a→b, a→c.
        prevs: Backward adjacency — prevs[b] = [a] means a→b.

    Returns:
        Nodes in topological order.
    """
    in_degree = {name: len(prevs.get(name, [])) for name in nodes}
    queue = [name for name, deg in in_degree.items() if deg == 0]
    order = []
    while queue:
        name = queue.pop(0)
        order.append(name)
        for succ in nexts.get(name, []):
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)
    return order


def topo_rank(
    ops: Dict[str, object],
    prevs: Dict[str, List[str]],
) -> Dict[str, int]:
    """Compute topological rank for each node (position in topo order).

    Unlike topo_sort, this works with an ops dict (name → object) and returns
    ranks keyed by ``op.full_name`` (via getattr). Nodes not in ``ops`` or
    without ``full_name`` are skipped.

    Args:
        ops: Mapping of short name → op object (must have ``.full_name``).
        prevs: Backward adjacency — prevs[b] = [a] means a→b.

    Returns:
        Dict mapping ``op.full_name`` → integer rank (0-based).
    """
    in_degree: Dict[str, int] = {name: 0 for name in ops}
    fwd: Dict[str, List[str]] = defaultdict(list)
    for name, preds in prevs.items():
        if name not in in_degree:
            continue
        for p in preds:
            if p in in_degree:
                fwd[p].append(name)
                in_degree[name] = in_degree.get(name, 0) + 1

    queue = sorted(n for n, d in in_degree.items() if d == 0)
    ranks: Dict[str, int] = {}
    rank = 0
    while queue:
        cur = queue.pop(0)
        op_obj = ops.get(cur)
        if op_obj:
            full = getattr(op_obj, "full_name", None)
            if full:
                ranks[full] = rank
                rank += 1
        for succ in sorted(fwd.get(cur, [])):
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)
    return ranks


def find_cycles(
    nodes: List[str],
    adj: Dict[str, List[str]],
) -> List[List[str]]:
    """Detect cycles via DFS coloring.

    Args:
        nodes: All node names.
        adj: Forward adjacency list.

    Returns:
        List of cycles, each cycle is a list of node names forming a loop.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {name: WHITE for name in nodes}
    cycles: List[List[str]] = []

    def dfs(node: str, path: List[str]) -> bool:
        color[node] = GRAY
        path.append(node)
        for neighbor in adj.get(node, []):
            if neighbor not in color:
                continue
            if color[neighbor] == GRAY:
                cycles.append(path[path.index(neighbor) :] + [neighbor])
                return True
            if color[neighbor] == WHITE and dfs(neighbor, path):
                return True
        path.pop()
        color[node] = BLACK
        return False

    for node in nodes:
        if color[node] == WHITE:
            dfs(node, [])

    return cycles


def reachable(
    starts: List[str],
    adj: Dict[str, List[str]],
    extra_neighbors: Optional[Callable[[str], List[str]]] = None,
) -> Set[str]:
    """Compute all nodes reachable from start nodes via DFS.

    Args:
        starts: Starting nodes for traversal.
        adj: Adjacency list (forward or backward).
        extra_neighbors: Optional callback returning additional neighbors
            for a node (e.g., branch candidates). Called as extra_neighbors(node).

    Returns:
        Set of reachable node names.
    """
    visited: Set[str] = set()

    def dfs(node: str):
        if node in visited:
            return
        visited.add(node)
        for neighbor in adj.get(node, []):
            dfs(neighbor)
        if extra_neighbors:
            for neighbor in extra_neighbors(node):
                dfs(neighbor)

    for start in starts:
        dfs(start)

    return visited


# ── Tree algorithms ──────────────────────────────────────────────────────────


def build_children(
    keys: List[str],
    parent_fn: Callable[[str], Optional[str]],
) -> Dict[Optional[str], List[str]]:
    """Group keys by parent into a children map.

    Args:
        keys: All node keys.
        parent_fn: Function returning the parent key for a given key
            (None for roots).

    Returns:
        Dict mapping parent_key → list of child keys. Roots are under None.
    """
    children: Dict[Optional[str], List[str]] = defaultdict(list)
    for key in keys:
        children[parent_fn(key)].append(key)
    return children


def tree_walk(
    roots: List[str],
    children_map: Dict[Optional[str], List[str]],
    sort_key: Optional[Callable[[str], object]] = None,
) -> List[str]:
    """Parent-first DFS traversal of a tree, optionally sorting siblings.

    Args:
        roots: Root node keys.
        children_map: Mapping parent_key → list of child keys.
        sort_key: Optional sort key function for ordering siblings.

    Returns:
        List of keys in parent-first DFS order.
    """
    result: List[str] = []

    def _visit(key: str) -> None:
        result.append(key)
        kids = children_map.get(key, [])
        if sort_key:
            kids = sorted(kids, key=sort_key)
        for child in kids:
            _visit(child)

    ordered_roots = sorted(roots, key=sort_key) if sort_key else roots
    for root in ordered_roots:
        _visit(root)

    return result


def prune_leaves(
    children_map: Dict[Optional[str], List[str]],
    predicate: Callable[[str], bool],
) -> Set[str]:
    """Remove leaf nodes matching predicate, recursively.

    A leaf is a key that appears in a parent's children list but has no
    children of its own (not a key in children_map, or has empty list).
    After removing it, its parent may become a new leaf and be pruned too.

    Mutates children_map in place.

    Args:
        children_map: Mutable mapping parent_key → list of child keys.
        predicate: Returns True for nodes eligible for pruning.

    Returns:
        Set of pruned keys.
    """
    pruned: Set[str] = set()
    changed = True
    while changed:
        changed = False
        for parent in list(children_map.keys()):
            kids = children_map.get(parent, [])
            new_kids = [k for k in kids if children_map.get(k) or not predicate(k)]
            removed = set(kids) - set(new_kids)
            if removed:
                pruned.update(removed)
                changed = True
                if new_kids:
                    children_map[parent] = new_kids
                elif parent is not None:
                    del children_map[parent]
                else:
                    children_map[parent] = []
    return pruned


def collapse(
    children_map: Dict[Optional[str], List[str]],
    predicate: Callable[[str], bool],
    on_collapse: Optional[Callable[[str, str], None]] = None,
) -> Set[str]:
    """Collapse single-child nodes matching predicate.

    If a node matches ``predicate`` and has exactly 1 child, the child
    replaces it in the parent's children list. The collapsed node is
    removed from children_map.

    Mutates children_map in place.

    Args:
        children_map: Mutable mapping parent_key → list of child keys.
        predicate: Returns True for nodes eligible for collapsing.
        on_collapse: Optional callback ``(collapsed_key, child_key)`` called
            before removal, so callers can update display names etc.

    Returns:
        Set of collapsed (removed) keys.
    """
    collapsed: Set[str] = set()
    for parent in list(children_map.keys()):
        kids = list(children_map.get(parent, []))
        new_kids = []
        for k in kids:
            sub_kids = children_map.get(k, [])
            if len(sub_kids) == 1 and predicate(k):
                child = sub_kids[0]
                if on_collapse:
                    on_collapse(k, child)
                new_kids.append(child)
                del children_map[k]
                collapsed.add(k)
            else:
                new_kids.append(k)
        children_map[parent] = new_kids
    return collapsed
