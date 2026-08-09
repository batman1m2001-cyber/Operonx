"""Phase 3 Level-2 rewrite: user-written back-edges → hidden GraphOp.loop.

Runs during ``GraphOp.build()`` **before** validation and auto-soft. Detects
back-edges (a `u >> v` where `v` is already on the DFS path), groups them by
strongly-connected component (Tarjan), and moves each SCC into a synthetic
``GraphOp.loop`` child. The outer graph then sees a DAG.

Termination is scheduler-native (no marker cells, no op wrapping). The
synthetic loop stores its back-edge source op names in
``_back_edge_sources``; the scheduler tracks which of them fired during the
iteration and terminates the loop when none did.

Public API: :func:`rewrite_cycles_to_loops`. Opt out with
``@graph(strict_dag=True)`` — the decorator sets ``_strict_dag=True`` on the
GraphOp and this pass is skipped.

See ``docs/design/STATE_LOOP_REFACTOR_PLAN.md`` §Phase 3.
"""

from typing import TYPE_CHECKING, Dict, List, Set, Tuple

from operonx.core.configs.edge_config import EdgeConfig
from operonx.core.loggings import LOGGER

if TYPE_CHECKING:
    from operonx.core.ops.graph.graph_op import GraphOp


# ---------------------------------------------------------------------------
# Back-edge detection
# ---------------------------------------------------------------------------


def _forward_adj(graph: "GraphOp") -> Dict[str, List[str]]:
    """Adjacency list from graph edges, ignoring lookback edges.

    Lookback edges are user-declared "intentional cycles" that predate this
    pass and remain excluded from cycle detection (matching the classic
    behavior in ``validation._validate_cycles``).
    """
    adj: Dict[str, List[str]] = {name: [] for name in graph._ops}
    for (src, dst), edge in graph._edges.items():
        if edge.type == "lookback":
            continue
        if src in adj and dst in adj:
            adj[src].append(dst)
    return adj


def detect_back_edges(graph: "GraphOp") -> List[Tuple[str, str]]:
    """DFS-colour back-edge detection.

    Returns list of ``(u, v)`` where ``u >> v`` is a back-edge (``v`` is an
    ancestor of ``u`` on the DFS stack). Deterministic order: children walked
    in insertion order (matches ``nexts`` list order).
    """
    adj = _forward_adj(graph)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {n: WHITE for n in graph._ops}
    back: List[Tuple[str, str]] = []

    def dfs(node: str) -> None:
        color[node] = GRAY
        for nb in adj.get(node, []):
            if nb not in color:
                continue
            if color[nb] == GRAY:
                back.append((node, nb))
            elif color[nb] == WHITE:
                dfs(nb)
        color[node] = BLACK

    # Iterate in _ops insertion order so back-edge discovery is deterministic.
    for n in list(graph._ops):
        if color[n] == WHITE:
            dfs(n)
    return back


# ---------------------------------------------------------------------------
# Tarjan SCC (iterative to avoid recursion-depth limits on wide graphs)
# ---------------------------------------------------------------------------


def tarjan_sccs(nodes: List[str], adj: Dict[str, List[str]]) -> List[List[str]]:
    """Iterative Tarjan. Returns SCCs (each a list of node names) whose
    membership size is >=2 OR whose single node has a self-loop.

    Order: reverse topological order of the SCC DAG (roots last). We reverse
    at the end so callers get earliest-discovered SCCs first, matching
    ``detect_back_edges`` ordering intuition.
    """
    index = 0
    stack: List[str] = []
    on_stack: Set[str] = set()
    indices: Dict[str, int] = {}
    lowlinks: Dict[str, int] = {}
    result: List[List[str]] = []

    def strongconnect(root: str) -> None:
        nonlocal index
        # Iterative DFS: each frame is (node, iterator over its successors).
        work_stack: List[Tuple[str, "iter"]] = []

        def _open(v: str) -> None:
            nonlocal index
            indices[v] = index
            lowlinks[v] = index
            index += 1
            stack.append(v)
            on_stack.add(v)
            work_stack.append((v, iter(adj.get(v, []))))

        def _close(v: str) -> None:
            if lowlinks[v] == indices[v]:
                scc = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == v:
                        break
                # Filter: skip single-node SCCs unless self-loop.
                if len(scc) > 1 or v in adj.get(v, []):
                    result.append(scc)

        _open(root)
        while work_stack:
            v, it = work_stack[-1]
            advanced = False
            for w in it:
                if w not in indices:
                    _open(w)
                    advanced = True
                    break
                elif w in on_stack:
                    lowlinks[v] = min(lowlinks[v], indices[w])
            if not advanced:
                work_stack.pop()
                _close(v)
                if work_stack:
                    parent = work_stack[-1][0]
                    lowlinks[parent] = min(lowlinks[parent], lowlinks[v])

    for n in nodes:
        if n not in indices:
            strongconnect(n)

    # Earliest discovered first.
    return list(reversed(result))


# ---------------------------------------------------------------------------
# SCC entry/exit analysis
# ---------------------------------------------------------------------------


def _scc_entry_and_back_edges(
    graph: "GraphOp",
    scc: Set[str],
    all_back_edges: List[Tuple[str, str]],
) -> Tuple[str, List[Tuple[str, str]]]:
    """Identify the loop entry op and the back-edges relevant to this SCC.

    The entry is the unique node in the SCC that has predecessors outside
    the SCC (typically ``START`` upstream). If more than one node has
    outside-SCC predecessors → E2 (multiple entries).
    """
    outside_preds: Dict[str, List[str]] = {n: [] for n in scc}
    for (src, dst), edge in graph._edges.items():
        if edge.type == "lookback":
            continue
        if dst in scc and src not in scc:
            outside_preds[dst].append(src)

    entries = [n for n, ps in outside_preds.items() if ps]

    # If nothing has an outside predecessor (self-contained SCC — e.g. graph is
    # just the loop, entered from START which is a sentinel not in _ops), fall
    # back to nodes marked as start=True in _ops, or to the back-edge target.
    if not entries:
        entries = [n for n in scc if graph._ops[n].start]

    scc_back = [(u, v) for (u, v) in all_back_edges if u in scc and v in scc]

    return (entries[0] if entries else None, entries, scc_back)


def _exits_from_scc(graph: "GraphOp", scc: Set[str]) -> List[Tuple[str, str]]:
    """Edges leaving the SCC — ``(src_in_scc, dst_outside)``. Deduplicated.
    Includes edges to ``__END__`` (which live as ``op.end = True`` markers or as
    branch-op candidates rather than as entries in ``_edges``).
    """
    seen: Set[Tuple[str, str]] = set()
    exits: List[Tuple[str, str]] = []

    def _add(pair):
        if pair not in seen:
            seen.add(pair)
            exits.append(pair)

    for (src, dst), edge in graph._edges.items():
        if edge.type == "lookback":
            continue
        if src in scc and dst not in scc:
            _add((src, dst))
    for n in scc:
        if graph._ops[n].end:
            _add((n, "__END__"))
    for n in scc:
        op = graph._ops[n]
        if op.type == "branch":
            for cand in getattr(op, "candidates", []) or []:
                if cand == "__END__":
                    _add((n, "__END__"))
                elif cand in graph._ops and cand not in scc:
                    _add((n, cand))
    return exits


# ---------------------------------------------------------------------------
# Main rewrite pass
# ---------------------------------------------------------------------------


def rewrite_cycles_to_loops(graph: "GraphOp") -> bool:
    """Rewrite every cyclic SCC in ``graph`` into a synthetic hidden loop.

    Returns True if any rewrite happened. Idempotent when re-run on a DAG.

    Runs BEFORE validation and auto-soft. Recurses into nested cycles
    (extracted body may itself be cyclic).

    Raises:
        ValueError: E1/E2/E4 build-time errors. E3 (multi-exit) is allowed.
    """
    from operonx.core.ops.graph.graph_op import GraphOp

    back_edges = detect_back_edges(graph)
    if not back_edges:
        return False

    if getattr(graph, "_strict_dag", False):
        # strict_dag graphs opt out — cycles bubble up to validate() as
        # warnings (matching pre-Phase-3 behavior).
        return False

    adj = _forward_adj(graph)
    sccs = tarjan_sccs(list(graph._ops), adj)
    cyclic_sccs = [scc for scc in sccs if len(scc) > 1 or scc[0] in adj.get(scc[0], [])]
    if not cyclic_sccs:
        return False

    audit: Dict[str, Dict] = {}
    loop_idx = 0

    for scc_list in cyclic_sccs:
        scc = set(scc_list)
        entry_first, entries, scc_back = _scc_entry_and_back_edges(graph, scc, back_edges)

        # E2: multiple entries into the SCC.
        if len(entries) > 1:
            raise ValueError(
                f"Graph '{graph.name}': cycle body {sorted(scc)} has {len(entries)} entries "
                f"({sorted(entries)}); extract entry into a merge node."
            )

        if not scc_back:
            # SCC formed only by lookback edges — nothing to rewrite here.
            continue

        exits = _exits_from_scc(graph, scc)
        if not exits:
            raise ValueError(
                f"Graph '{graph.name}': cycle {sorted(scc)} has no exit — would loop forever. "
                f"Add an ``if_(cond, END)`` branch inside the loop body."
            )

        # E4: back-edge crossing subgraph boundary — the SCC includes nodes
        # from a nested graph. Detect by finding an op whose .parent isn't
        # this graph.
        for n in scc:
            if graph._ops[n].parent is not graph:
                raise ValueError(
                    f"Graph '{graph.name}': cyclic edge involving '{n}' crosses "
                    f"@graph boundary — not supported."
                )

        loop_name = _fresh_loop_name(graph, loop_idx)
        loop_idx += 1

        hidden = _synthesize_loop(
            outer=graph,
            loop_name=loop_name,
            scc=scc,
            entry=entry_first,
            scc_back_edges=scc_back,
            exits=exits,
        )

        audit[loop_name] = {
            "scc": sorted(scc),
            "entry": entry_first,
            "back_edges": scc_back,
            "exits": exits,
        }

        # Recurse: the hidden loop's body may itself contain nested cycles.
        rewrite_cycles_to_loops(hidden)

    if audit:
        graph._rewritten_from = audit
        LOGGER.info(
            "Graph [highlight]%s[/highlight]: rewrote [highlight]%d[/highlight] cyclic SCC(s) "
            "to hidden loop(s): %s",
            graph.name,
            len(audit),
            list(audit),
        )
    return bool(audit)


def _fresh_loop_name(graph: "GraphOp", idx: int) -> str:
    """Pick a unique hidden-loop op name inside ``graph._ops``."""
    base = f"__loop_{idx}__"
    if base not in graph._ops:
        return base
    i = idx + 1
    while f"__loop_{i}__" in graph._ops:
        i += 1
    return f"__loop_{i}__"


def _synthesize_loop(
    outer: "GraphOp",
    loop_name: str,
    scc: Set[str],
    entry: str,
    scc_back_edges: List[Tuple[str, str]],
    exits: List[Tuple[str, str]],
) -> "GraphOp":
    """Extract SCC into a hidden GraphOp and rewire outer edges to point at it.

    Post-conditions on ``outer``:
      - SCC ops removed from ``outer._ops`` and re-parented into the hidden loop.
      - Back-edges deleted (they become implicit iteration signals).
      - Incoming outer edges to any SCC node re-target the hidden loop.
      - Outgoing edges from SCC nodes to non-END targets re-source the loop.
      - Edges to ``__END__`` from SCC ops promote the hidden loop to end=True
        AND the entry-inside-loop retains end=True (so the loop body reaches
        an internal end).

    Post-conditions on ``hidden``:
      - ``_ops`` = SCC ops.
      - ``_edges`` = original SCC-internal edges minus back-edges.
      - ``entry`` is marked ``.start = True``; SCC nodes that had exits are
        marked ``.end = True`` inside the loop.
      - ``_back_edge_sources = {u for (u,_) in scc_back_edges}``.
      - ``_synthetic = True``, ``_loop_mode = "synthetic"``.
    """
    from operonx.core.ops.graph.graph_op import GraphOp

    hidden = GraphOp(name=loop_name)
    hidden._synthetic = True
    hidden._loop_mode = "synthetic"
    hidden._back_edge_sources = {u for (u, _v) in scc_back_edges}
    # Configure loop-config in synthetic mode: no _evaluate_until (scheduler
    # uses _back_edge_sources), high default max_iterations.
    from operonx.core.ops.graph.task_scheduler import LoopConfig

    hidden._loop_config = LoopConfig(until=None, max_iterations=1000)

    # --- Move SCC ops into hidden ------------------------------------------------
    for n in scc:
        child = outer._ops.pop(n)
        child.parent = hidden
        # Reset start/end; we'll re-mark below based on the extracted structure.
        child.start = False
        child.end = False
        child._full_name = None
        hidden._ops[n] = child
        # Purge outer entry/exit references to the moved op — they now live
        # in the hidden loop only.
        if n in outer.entries:
            outer.entries.remove(n)
        if n in outer.exits:
            outer.exits.remove(n)

    # --- Move SCC-internal edges (minus back-edges) into hidden ------------------
    back_set = set(scc_back_edges)
    for key in list(outer._edges):
        src, dst = key
        if src in scc and dst in scc:
            edge = outer._edges.pop(key)
            outer.nexts[src].remove(dst)
            outer.prevs[dst].remove(src)
            if (src, dst) in back_set:
                # Back-edge dropped entirely — the scheduler infers the
                # iteration signal from _back_edge_sources activation.
                continue
            hidden._edges[key] = EdgeConfig(
                from_node=src, to_node=dst, type=edge.type, soft=edge.soft,
                pinned_hard=edge.pinned_hard,
            )
            hidden.nexts[src].append(dst)
            hidden.prevs[dst].append(src)

    # --- Mark loop entry --------------------------------------------------------
    hidden._ops[entry].start = True
    if entry not in hidden.entries:
        hidden.entries.append(entry)

    # --- Rewire incoming outer edges to the hidden loop -------------------------
    for key in list(outer._edges):
        src, dst = key
        if dst not in scc:
            continue
        # We only kept outer→SCC edges — the SCC-internal ones already moved.
        edge = outer._edges.pop(key)
        outer.nexts[src].remove(dst)
        outer.prevs[dst].remove(src)
        # Point at the hidden loop's name instead.
        new_key = (src, loop_name)
        if new_key not in outer._edges:
            outer._edges[new_key] = EdgeConfig(
                from_node=src, to_node=loop_name, type=edge.type,
                soft=edge.soft, pinned_hard=edge.pinned_hard,
            )
            outer.nexts[src].append(loop_name)
            outer.prevs[loop_name].append(src)

    # Handle START markers: an SCC entry that was start=True in outer means
    # the outer graph's START >> entry should now be START >> hidden.
    # (start marker moved when we reset child.start above, so re-mark hidden.)
    if entry in outer.entries:
        outer.entries.remove(entry)
    # Outer entry updates for cases where entry was declared via START marker
    # only (no explicit edge). The plan considers this rare — but we cover it
    # by inspecting the outer graph's initial entries.

    # --- Rewire outgoing edges to non-END outside targets -----------------------
    hidden_ends: Set[str] = set()  # nodes inside hidden that carry .end=True
    for (src_in_scc, dst) in exits:
        if dst == "__END__":
            hidden._ops[src_in_scc].end = True
            hidden_ends.add(src_in_scc)
            continue
        # Move outer edge (src_in_scc → dst) to (loop_name → dst).
        key = (src_in_scc, dst)
        if key in outer._edges:
            edge = outer._edges.pop(key)
            outer.nexts[src_in_scc].remove(dst)
            outer.prevs[dst].remove(src_in_scc)
            new_key = (loop_name, dst)
            if new_key not in outer._edges:
                outer._edges[new_key] = EdgeConfig(
                    from_node=loop_name, to_node=dst, type=edge.type,
                    soft=edge.soft, pinned_hard=edge.pinned_hard,
                )
                outer.nexts[loop_name].append(dst)
                outer.prevs[dst].append(loop_name)
        # Inside the loop, mark src_in_scc as an internal end (it terminates
        # the DAG portion of the iteration).
        hidden._ops[src_in_scc].end = True
        hidden_ends.add(src_in_scc)

    for n in hidden_ends:
        if n not in hidden.exits:
            hidden.exits.append(n)

    # Back-edge SOURCES become implicit iteration-signals. Mark them as
    # internal loop ends so the DAG scheduler can terminate the iteration
    # when they finish (otherwise they'd be dead-ends inside the loop body).
    for u in hidden._back_edge_sources:
        hidden._ops[u].end = True
        if u not in hidden.exits:
            hidden.exits.append(u)

    # Register the hidden loop as an op of the outer graph.
    outer._ops[loop_name] = hidden
    hidden.parent = outer
    hidden._full_name = None

    # If outer graph was headed for END from this SCC only, ensure the outer
    # end-marker moves to the hidden loop's descendants; otherwise the loop
    # must at least reach one of its non-END exits above.
    if not any(dst == "__END__" for _s, dst in exits):
        # Pure back-edge SCC with no END exit — user must have connected the
        # SCC's outputs to some non-END node OR the loop just terminates and
        # nothing runs after. Fine either way for outer topology.
        pass
    else:
        # Loop node inherits end=True; outer graph will route it to END.
        outer._ops[loop_name].end = True
        if loop_name not in outer.exits:
            outer.exits.append(loop_name)

    return hidden
