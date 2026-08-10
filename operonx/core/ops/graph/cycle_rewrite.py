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
    the SCC (typically ``START`` upstream). Additionally, per E8 in the
    plan's E-table, if the SCC's back-edges TARGET distinct nodes, that
    also counts as "multiple entries" and must be user-resolved via a merge
    node — otherwise the rewrite would silently pick one target and drop
    the others' iteration paths.
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

    # E8: if back-edges target multiple distinct nodes in the SCC, each is
    # implicitly a separate iteration entry — merge upstream so the loop has
    # one entry.
    back_targets = {v for _u, v in scc_back}
    if len(back_targets) > 1:
        # Union entries with back-targets so E2 error message enumerates them.
        entries = sorted(set(entries) | back_targets)

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
        # Preserve Tarjan's node order (list, not set) so hidden._ops
        # insertion is deterministic across runs — set iteration is hash-
        # order dependent (Phase 3 review HAZARD: nondet iteration).
        scc_seq: List[str] = list(scc_list)
        scc: Set[str] = set(scc_seq)
        entry_first, entries, scc_back = _scc_entry_and_back_edges(
            graph, scc, back_edges
        )

        # E2 (+ E8): multiple entries into the SCC — either from outside
        # preds or from back-edges targeting distinct nodes.
        if len(entries) > 1:
            raise ValueError(
                f"Graph '{graph.name}': cycle body {sorted(scc)} has {len(entries)} entries "
                f"({sorted(entries)}); extract entry into a merge node."
            )
        # E1 stricter surface: no entry could be determined (HAZARD from
        # Phase 3 review: previously raised a raw KeyError deep in
        # _synthesize_loop when we tried to mark None as .start).
        if entry_first is None:
            raise ValueError(
                f"Graph '{graph.name}': cannot determine loop entry for cycle body "
                f"{sorted(scc)} — no outside predecessor and no op with start=True."
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
        for n in scc_seq:
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
            scc_seq=scc_seq,
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
    scc_seq: List[str],
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
    # Preserve source AND target of each back-edge, not just the source, so
    # the scheduler can decide "would this back-edge have fired" per iter
    # correctly for the (source-is-branch) case (BUG 1 from Phase 3 review):
    # branch ops write end_time regardless of which target they picked, so
    # source-fired alone is always True. Termination consults the branch's
    # __branch_target__ output when the source is a branch op.
    hidden._back_edges = list(scc_back_edges)
    hidden._back_edge_sources = {u for (u, _v) in scc_back_edges}  # kept for audit
    from operonx.core.ops.graph.task_scheduler import LoopConfig

    hidden._loop_config = LoopConfig(until=None, max_iterations=1000)

    # --- Move SCC ops into hidden ------------------------------------------------
    # Iterate over scc_seq (list) not scc (set) so hidden._ops insertion is
    # deterministic across runs (Phase 3 review HAZARD: nondet iteration).
    entry_was_outer_entry = False
    entry_was_outer_exit = False
    for n in scc_seq:
        child = outer._ops.pop(n)
        child.parent = hidden
        # Reset start/end; we'll re-mark below based on the extracted structure.
        child.start = False
        child.end = False
        child._full_name = None
        hidden._ops[n] = child
        # Purge outer entry/exit references to the moved op — they now live
        # in the hidden loop only. Remember whether the entry itself was an
        # outer entry/exit so we can promote the hidden loop node to fill
        # the same role (HAZARD from Phase 3 review: outer.entries/exits
        # lost the loop op when other unrelated entries/exits remained,
        # skipping _setup_endpoints's fallback path).
        if n in outer.entries:
            outer.entries.remove(n)
            if n == entry:
                entry_was_outer_entry = True
        if n in outer.exits:
            outer.exits.remove(n)
            if n == entry:
                entry_was_outer_exit = True

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
                # iteration signal from _back_edges activation.
                continue
            hidden._edges[key] = EdgeConfig(
                from_node=src, to_node=dst, type=edge.type, soft=edge.soft,
                pinned_hard=edge.pinned_hard,
            )
            hidden.nexts[src].append(dst)
            hidden.prevs[dst].append(src)

    # --- Rewire SCC→outer edges (any type, including lookback) ------------------
    # BUG 5 fix: pre-hardening, lookback edges from an SCC node to an outer
    # node were silently left in outer._edges after the src had been moved
    # into the hidden loop — a dangling edge whose src key no longer exists.
    # Move ALL non-internal SCC-src edges through the loop, preserving type.
    for key in list(outer._edges):
        src, dst = key
        if src not in scc or dst in scc:
            continue
        edge = outer._edges.pop(key)
        outer.nexts[src].remove(dst)
        outer.prevs[dst].remove(src)
        new_key = (loop_name, dst)
        if new_key not in outer._edges:
            outer._edges[new_key] = EdgeConfig(
                from_node=loop_name, to_node=dst, type=edge.type,
                soft=edge.soft, pinned_hard=edge.pinned_hard,
            )
            outer.nexts[loop_name].append(dst)
            outer.prevs[dst].append(loop_name)

    # --- Mark loop entry --------------------------------------------------------
    hidden._ops[entry].start = True
    if entry not in hidden.entries:
        hidden.entries.append(entry)

    # --- Rewire incoming outer edges to the hidden loop -------------------------
    entry_had_start_marker = False
    for key in list(outer._edges):
        src, dst = key
        if dst not in scc:
            continue
        # We only kept outer→SCC edges — the SCC-internal ones already moved.
        edge = outer._edges.pop(key)
        outer.nexts[src].remove(dst)
        outer.prevs[dst].remove(src)
        if src == "__START__":
            entry_had_start_marker = True
        # Point at the hidden loop's name instead.
        new_key = (src, loop_name)
        if new_key not in outer._edges:
            outer._edges[new_key] = EdgeConfig(
                from_node=src, to_node=loop_name, type=edge.type,
                soft=edge.soft, pinned_hard=edge.pinned_hard,
            )
            outer.nexts[src].append(loop_name)
            outer.prevs[loop_name].append(src)

    # Promote the hidden loop into outer.entries when the SCC entry was
    # itself an outer entry (marked by START >> entry). This is either
    # detected during the SCC-op move above OR from the START-src edge check
    # in the incoming-rewire block (some START connections travel through
    # the edge list too depending on how they were declared).
    if (entry_was_outer_entry or entry_had_start_marker) and loop_name not in outer.entries:
        outer.entries.append(loop_name)

    # --- Mark loop-body internal ends + cover branch string candidates ----------
    # By this point, the "Rewire SCC→outer edges" block has moved every real
    # outer edge from an SCC node to (loop_name → dst). We still need to:
    #   1. Mark ops inside the loop that terminate the DAG portion as .end=True
    #      (their EOF triggers the outer loop iteration check).
    #   2. Ensure outer._edges (loop_name → dst) exists for every exit —
    #      including branch string-name candidates that had no auto-added
    #      edge to begin with (HAZARD from Phase 3 review: named-string
    #      branch candidates skipped by rewire).
    hidden_ends: Set[str] = set()  # nodes inside hidden that carry .end=True
    for (src_in_scc, dst) in exits:
        if dst == "__END__":
            hidden._ops[src_in_scc].end = True
            hidden_ends.add(src_in_scc)
            continue
        # Synthesize the outer edge if the SCC→outer rewire pass didn't
        # already produce it (covers string-name branch candidates).
        new_key = (loop_name, dst)
        if new_key not in outer._edges and dst in outer._ops:
            outer._edges[new_key] = EdgeConfig(
                from_node=loop_name, to_node=dst,
            )
            outer.nexts[loop_name].append(dst)
            outer.prevs[dst].append(loop_name)
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
