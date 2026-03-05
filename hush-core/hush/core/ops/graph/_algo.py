"""Reusable graph walk algorithms for DAG analysis."""

from typing import Callable, Dict, List, Optional, Set


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
    cycles = []

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
