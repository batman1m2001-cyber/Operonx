"""GraphOp — container op that manages a graph of child ops.

Package layout::

    graph_op.py      GraphOp class (define + build + run + export)
    task_scheduler.py LoopConfig, Scheduler
    _decorators.py   @graph and @graph.loop decorators
    validation.py    Graph validation rules and error types
"""

import asyncio
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, AsyncGenerator, Dict, List, NamedTuple, Optional, Tuple

from operonx.core.configs.edge_config import EdgeConfig, EdgeType
from operonx.core.configs.op_config import OpType
from operonx.core.loggings import LOGGER
from operonx.core.ops._events import ERROR_EDGE_VARS, Failure
from operonx.core.ops.base import END, PARENT, START, BaseOp
from operonx.core.ops.graph.task_scheduler import (
    LoopConfig,
    Scheduler,
)

# Live, not back-compat: the validation types are imported from here by
# the graph_op tests, and `validate_graph` is called by this file.
from operonx.core.ops.graph.validation import (  # noqa: E402, F401
    GraphValidationError,
    ValidationIssue,
    ValidationLevel,
    ValidationResult,
    validate_graph,
)
from operonx.core.policy import _Deadline, fail_fast
from operonx.core.states import MemoryState, Ref
from operonx.core.states.cell import DEFAULT_CONTEXT
from operonx.core.utils.common import Param
from operonx.core.utils.context import _current_graph

# The two arms of an op with error edges (`op.on_error(handler)`), as seen
# by `_arm_signatures`. Tuples, so they never equal a branch arm (an op name).
_OK_ARM = ("ok",)
_ERROR_ARM = ("error",)


class Link(NamedTuple):
    dst: str
    soft: bool


class GraphOp(BaseOp):
    """Container op that holds and executes a directed graph of child ops.

    Lifecycle::

        1. DEFINE        with GraphOp(name="wf") as g:
                             a = double(x=PARENT["x"])
                             b = add(a=a["result"], b=PARENT["y"])
                             START >> a >> b >> END
                         Ops auto-register via context manager. Edges via >> operator.
                         Inputs/outputs auto-discovered from PARENT refs.

        2. BUILD         g.build()  (or auto on first run)
                         _setup_schema    scan PARENT refs → graph inputs/outputs
                         _setup_endpoints find entry/exit ops from topology
                         _build()         adj list + ready counts + stream ready counts
                         validate         branch targets, cycles, reachability, refs

        3. EXECUTE       g.run(state, context_id)  — async generator
                         → run_task_scheduler()  drives ops via Frame/EOF events
                         → yields (ctx, outputs) per batch or per stream frame
                         → loop iteration handled inside scheduler EOF handler

        4. EXPORT        serialize()  the graph as a config dict
                         validate()   graph structure validation
                         show()       debug display
    """

    __slots__ = [
        "_token",
        "_ops",
        "entries",
        "exits",
        "prevs",
        "nexts",
        "_edges",
        "_is_building",
        "concurrency",
        "_loop_config",
        "_shared_vars",
        "_reducer_vars",
        "_race_vars",
        "_adj",
        "_err_adj",
        "_initial_ready",
        "_stream_initial_ready",
        "_scheduler",
        "_out_vars",
        "_output_writers",
        "_auto_soft",
        # Phase 3 cycle-rewrite plumbing
        "_strict_dag",
        "_synthetic",
        "_loop_mode",
        "_back_edge_sources",
        "_back_edges",
        "_exit_edges",
        "_rewritten_from",
    ]

    type: OpType = "graph"

    # ═══════════════════════════════════════════════════════════════════
    # 1. DEFINE — build the graph structure
    # ═══════════════════════════════════════════════════════════════════

    def __init__(
        self,
        concurrency: int = 64,
        auto_soft: bool = True,
        strict_dag: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        if self._policy is not None:
            self._check_graph_policy(*self._policy)
        self._token = None
        self._is_building = True
        self._ops: Dict[str, BaseOp] = {}
        self._edges = {}
        self.entries = []
        self.exits = []
        self.prevs = defaultdict(list)
        self.nexts = defaultdict(list)
        self.concurrency = concurrency
        self._loop_config = None
        self._shared_vars = {}  # {var_name: initial_value} — set by PARENT.shared() or PARENT.declare()
        self._reducer_vars = {}  # {var_name: reducer_fn} — set by PARENT.declare(reducers=...)
        # Declared vars whose writers may race: PARENT.declare(..., allow_race=True).
        self._race_vars: set = set()
        self._adj = {}  # {op_name: [Link(dst, soft), ...]}
        # {op_name: ((handler, vars), ...)} — error edges, `op.on_error(h)`:
        # followed only when op fails; `vars` are the handler's unwired
        # `error` / `op` / `inputs` parameters the failure is written into.
        self._err_adj: Dict[str, tuple] = {}
        self._initial_ready = {}  # {op_name: ready_count}
        self._stream_initial_ready = {}  # {gen_name: {op_name: ready_count_for_stream_ctx}}
        self._scheduler = None  # set by build()
        self._out_vars: Dict[
            str, dict
        ] = {}  # {op_name: {src_var: dest_var}} — vars mapped to PARENT output
        # {output_var: [ops that write it]} — set by _setup_schema; tells an
        # output its ops wrote from one that only holds the same-named input.
        self._output_writers: Dict[str, List[BaseOp]] = {}
        self._auto_soft = auto_soft  # auto-soften branch-merge edges at build time
        # Phase 3: opt-out for the Level-2 cycle→loop rewrite. When True, back-edges
        # remain as-is and hit the classic validate() warning path.
        self._strict_dag = strict_dag
        # Phase 3: hidden loops created by the cycle rewrite carry these markers.
        self._synthetic = False
        self._loop_mode = None  # None (classic), "synthetic" (rewritten hidden loop)
        self._back_edge_sources: set = set()  # audit only; termination consults _back_edges
        self._back_edges: list = []  # List[(u_name, v_name)] for termination per back-edge
        # List[(u_name, dst)]: an edge from loop-body op u to dst outside the
        # loop. The scheduler routes the loop op to dst, once, when the loop
        # exits — and only if u took that edge in the final iteration.
        self._exit_edges: list = []
        self._rewritten_from = None  # audit dict populated by rewrite_cycles_to_loops

    def _check_graph_policy(self, retry, timeout) -> None:
        """A graph used as an op takes ``timeout=Timeout(run=...)``, and nothing else.

        ``retry=`` would never fire: an op inside the graph that raises is
        recorded by that op and the graph ends without raising. ``idle`` is
        the time between a generator's yields, and a graph is not one.
        """
        if retry is not None:
            raise TypeError(
                f"Graph '{self.name}': retry= applies to an op, not to a graph — the ops "
                f"inside record their own failures, so the graph has nothing to retry. "
                f"Put retry=Retry(...) on the op inside that can fail."
            )
        if timeout.idle is not None:
            raise ValueError(
                f"Graph '{self.name}': Timeout(idle=...) is the time between a generator's "
                f"yields; a graph takes Timeout(run=...)."
            )

    def __enter__(self):
        """Enter context manager mode — ops created inside are auto-registered."""
        self._token = _current_graph.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager mode — discover schema on clean exit."""
        _current_graph.reset(self._token)
        if exc_type is None:
            self._setup_schema()

    # NOTE (1.0.0): the classic ``GraphOp.loop(until=..., max_iterations=...)``
    # constructor was removed. Write feedback loops with a back-edge inside
    # ``@graph`` and let the Phase 3 cycle-rewrite pass synthesize the hidden
    # loop for you::
    #
    #     @graph
    #     def counter():
    #         inc = increment(counter=PARENT["count"])
    #         inc["counter"] >> PARENT["count"]
    #         START >> inc >> if_(PARENT["count"] >= 5, END).else_(inc)
    #
    # The synthesized ``_GraphLoop`` (internal-only) still lives as a private
    # GraphOp instance with ``_loop_mode = "synthetic"`` and ``_loop_config``
    # set by the rewrite pass; there is no user-facing constructor.

    @staticmethod
    def get_current_graph() -> Optional["GraphOp"]:
        """Return the current graph from context."""
        try:
            return _current_graph.get()
        except LookupError:
            return None

    def add_op(self, op: BaseOp) -> BaseOp:
        """Add an op to the graph."""
        if not self._is_building:
            raise RuntimeError("Cannot add op after graph has been built")

        if getattr(op, "_is_operonx_builder", False):
            name = getattr(op, "_name", None) or type(op).__name__
            LOGGER.error(
                "%s '%s' is not built. Call .build() or .else_() before adding to graph.",
                type(op).__name__,
                name,
            )
            raise TypeError(
                f"{type(op).__name__} '{name}' is not built. "
                f"Call .build() or .else_() to create the op."
            )

        if op in [START, END]:
            return op

        if op.name in self._ops:
            LOGGER.warning(
                "Graph [highlight]%s[/highlight]: op [highlight]%s[/highlight] already exists and will be overwritten",
                self.name,
                op.name,
            )

        self._ops[op.name] = op

        if hasattr(op, "start") and op.start:
            if op.name not in self.entries:
                self.entries.append(op.name)

        if hasattr(op, "end") and op.end:
            if op.name not in self.exits:
                self.exits.append(op.name)

        return op

    def add_edge(
        self,
        source: str,
        target: str,
        type: EdgeType = "normal",
        soft: bool = False,
        hard: bool = False,
    ):
        """Add an edge between two ops.

        Args:
            source: Source op name.
            target: Target op name.
            type: Edge type (normal, lookback, condition).
            soft: If True, edge does not count toward ready_count.
                  Used for branch outputs when only one branch executes.
            hard: If True, opt this edge out of auto-softening at build time.
                  Use for the rare case where a branch-descended pred must
                  still be waited on as a hard dependency.
        """
        if not self._is_building:
            raise RuntimeError("Cannot add edge after graph has been built!")

        if source == START.name:
            if target not in self._ops:
                raise ValueError(f"Target op '{target}' not found")

            target_node = self._ops[target]
            target_node.start = True

            if target not in self.entries:
                self.entries.append(target)

            return

        if target == END.name:
            if source not in self._ops:
                raise ValueError(f"Source op '{source}' not found")

            source_node = self._ops[source]
            source_node.end = True

            if source not in self.exits:
                self.exits.append(source)

            return

        if target == PARENT.name:
            return

        if source not in self._ops:
            raise ValueError(f"Source op '{source}' not found")
        if target not in self._ops:
            raise ValueError(f"Target op '{target}' not found")

        new_edge = EdgeConfig(
            from_node=source, to_node=target, type=type, soft=soft, pinned_hard=hard
        )
        if (source, target) not in self._edges:
            self._edges[source, target] = new_edge
            self.nexts[source].append(target)
            self.prevs[target].append(source)

    # ═══════════════════════════════════════════════════════════════════
    # 2. BUILD — compile graph for execution
    # ═══════════════════════════════════════════════════════════════════

    def build(self):
        """Build graph: cycle-rewrite → children → schema → endpoints → validation → topology.

        The cycle-rewrite pass (Phase 3 Level-2) runs FIRST so that any
        synthetic hidden ``GraphOp.loop`` children it creates are then built
        alongside the user's own children. Ordering matters — validate() and
        auto_soft assume a DAG and would produce wrong results on cyclic input.
        """
        # Phase 3: rewrite user-authored back-edges into hidden loop nodes
        # BEFORE building children (so the hidden loop children get built too).
        from operonx.core.ops.graph.cycle_rewrite import check_loop_caps, rewrite_cycles_to_loops

        rewrite_cycles_to_loops(self)
        check_loop_caps(self)

        for child in self._ops.values():
            if hasattr(child, "build"):
                child.build()

        self._setup_schema()
        self._setup_endpoints()

        result = self.validate()
        result.raise_if_errors()

        self._auto_soften_edges()

        self._build()

        self._scheduler = Scheduler(self)
        self._is_building = False
        self._cache_full_names()

        # Auto-detect bound from children, with user override.
        # If user explicitly set bound on the graph, respect it.
        # Otherwise: all children sync → graph is sync (inline); any io/cpu → task.
        # A policy — the graph's own timeout, a child's retry — needs a task:
        # inline, nothing could interrupt it or wait between attempts.
        if self.bound is None:
            if self._policy is None and all(
                getattr(op, "bound", None) == "sync" and getattr(op, "_policy", None) is None
                for op in self._ops.values()
            ):
                self.bound = "sync"
            else:
                self.bound = "io"

    def _arm_signatures(self):
        """``sig(op, merge) -> {decider: {arm, ...}}``, or None without deciders.

        A *decider* is an op whose successors do not all run: a branch, which
        takes one of its edges, and an op with error edges
        (``op.on_error(handler)``), which takes its normal edges when it
        succeeds and its error edges when it fails. ``sig(p, m)`` says
        through which arm of each decider ``p`` is reached; two ops whose
        arms of one decider are disjoint never both run. Used by
        :meth:`_auto_soften_edges` and the concurrent-writer check.

        A branch's arms are its successors. An op with error edges has two:
        everything after its normal edges, and everything after its error
        edges.
        """
        is_branch: Dict[str, bool] = {}
        for name, op in self._ops.items():
            if op.type == "branch":
                is_branch[name] = True
            elif any(
                self._edges[(name, dst)].type == "error"
                for dst in self.nexts.get(name, [])
                if (name, dst) in self._edges
            ):
                is_branch[name] = False
        if not is_branch:
            return None

        def arm(decider: str, succ: str):
            edge = self._edges.get((decider, succ))
            if edge is not None and edge.type == "error":
                return _ERROR_ARM
            return succ if is_branch[decider] else _OK_ARM

        # successor_reachable[decider][successor] = set of ops reachable from successor
        successor_reachable: Dict[str, Dict[str, set]] = {}
        for b in is_branch:
            successor_reachable[b] = {}
            for succ in self.nexts.get(b, []):
                seen = {succ}
                stack = [succ]
                while stack:
                    n = stack.pop()
                    for nxt in self.nexts.get(n, []):
                        if nxt not in seen:
                            seen.add(nxt)
                            stack.append(nxt)
                successor_reachable[b][succ] = seen

        def branch_sig(target_op: str, merge_name: Optional[str] = None) -> Dict[str, set]:
            """{decider: {arm of the decider that can reach target_op}}.

            The forward walk answers "which arm of B did this predecessor
            come through?", which needs the predecessor to be *downstream*
            of B. One predecessor is not: the branch itself, when it feeds
            the merge directly.

                B ──[cond]──> gate → ... → P ──┐
                  └─[else]─────────────────────┴──> M

            `P` reports arm `gate`. `B` reports nothing — reaching `B` from
            its own successors would need a cycle — so the pair shares no
            branch, is never found exclusive, and `M` deadlocks waiting for
            an arm that did not run. That is the shape of every "gate that
            can skip a step", so it is worth naming rather than leaving to
            a manual `~`.

            `B`'s edge into `M` does have an arm: it is `M`. Zero hops, but
            an arm all the same, and recording it makes the two signatures
            disjoint exactly when they should be.
            """
            sig: Dict[str, set] = {}
            for b, succ_reach in successor_reachable.items():
                arms = {arm(b, succ) for succ, reach in succ_reach.items() if target_op in reach}
                if arms:
                    sig[b] = arms
            if (
                merge_name is not None
                and target_op in successor_reachable
                and merge_name in self.nexts.get(target_op, [])
            ):
                sig.setdefault(target_op, set()).add(arm(target_op, merge_name))
            return sig

        return branch_sig

    def _auto_soften_edges(self):
        """Auto-soften edges from mutually-exclusive branch predecessors.

        For every op M with 2+ predecessors, if two predecessors trace back to
        a common ``BranchOp`` ancestor via *disjoint first-hop children*, they
        are mutually exclusive at runtime — only one fires per execution. This
        pass flips the incoming edges to ``soft`` so ``M``'s ready-count does
        not deadlock waiting for the branch that never ran.

        Without this pass, users have to manually mark those edges with ``~``
        (e.g., ``denoise >> ~picker``). Missing the ``~`` silently deadlocks
        at runtime, which is a common bug.

        Semantics:
        - Skipped for edges already marked ``soft=True`` (user's manual ``~``
          or ``>``).
        - Skipped for edges added via ``add_edge(..., hard=True)``.
        - Skipped entirely when the graph was constructed with
          ``auto_soft=False``.
        - Ancestor walk crosses both hard AND soft edges (branch ancestry is a
          topological fact; a soft edge upstream doesn't change which branch
          decides a downstream node).
        - Signatures are computed against the *original* edge structure BEFORE
          any softening is applied — flipping edges mid-analysis would erase
          branch attributions for downstream merges.

        A predecessor may also *be* the deciding branch — ``B >> M`` on one
        arm, ``B >> ... >> P >> M`` on another. See ``branch_sig``: the
        direct edge's arm is ``M`` itself, and without recording that, the
        pair shares no branch and ``M`` deadlocks.

        Known limitation: does not detect "sneak paths" (a predecessor
        reachable from a non-branch root that bypasses ``B`` entirely). In
        that case, the two predecessors may not truly be mutually exclusive
        and this pass over-softens. Use ``hard=True`` on the specific edge or
        ``auto_soft=False`` on the whole graph to opt out.
        """
        if not self._auto_soft:
            return

        # For each decider B — a branch, or an op with error edges, whose
        # handler and normal successors never both run — the arms of B
        # through which each op is reachable. See `_arm_signatures`.
        branch_sig = self._arm_signatures()
        if branch_sig is None:
            return

        audit_log = []

        for merge_name in list(self._ops.keys()):
            preds = self.prevs.get(merge_name, [])
            if len(preds) < 2:
                continue

            # Signatures per pred (computed against original edge structure).
            sigs = {p: branch_sig(p, merge_name) for p in preds}

            for p in preds:
                edge = self._edges.get((p, merge_name))
                # An error edge is its handler's trigger, never a merge arm.
                if edge is None or edge.soft or edge.pinned_hard or edge.type == "error":
                    continue
                witness = None
                for q in preds:
                    if q == p:
                        continue
                    for b in sigs[p].keys() & sigs[q].keys():
                        if sigs[p][b].isdisjoint(sigs[q][b]):
                            witness = (b, q)
                            break
                    if witness:
                        break
                if witness:
                    edge.soft = True
                    edge.auto_soft = True
                    audit_log.append((merge_name, p, witness[1], witness[0]))

        for merge_name, p, q, b in audit_log:
            LOGGER.debug(
                "Graph [highlight]%s[/highlight]: auto-soft [highlight]%s→%s[/highlight] "
                "(branch ancestor: [highlight]%s[/highlight], "
                "sibling pred: [highlight]%s[/highlight])",
                self.name,
                p,
                merge_name,
                b,
                q,
            )
        if audit_log:
            merge_points = {m for m, _, _, _ in audit_log}
            LOGGER.info(
                "Graph [highlight]%s[/highlight]: auto-softened "
                "[highlight]%d[/highlight] edges across [highlight]%d[/highlight] merge points",
                self.name,
                len(audit_log),
                len(merge_points),
            )

    def _build(self):
        """Compile adjacency list, batch ready counts, and per-generator stream ready counts."""
        # ── Phase 1: adjacency list + batch ready counts ──────────────────────────
        # For each op, count how many predecessors it must wait for (ready count).
        # Soft edges to the same target count as 1 (ANY one soft pred unblocks it).
        adj = {name: [] for name in self._ops}
        ready = {name: 0 for name in self._ops}
        has_soft: Dict[str, bool] = {}
        err_adj: Dict[str, list] = {}

        for (src, dst), edge in self._edges.items():
            if edge.type == "error":
                # Followed only on a failure, so not in `adj`, which every
                # frame walks; a hard predecessor of the handler all the same.
                handler = self._ops[dst]
                fed = tuple(
                    var
                    for var in ERROR_EDGE_VARS
                    if var in handler.inputs and handler.inputs[var].value is None
                )
                err_adj.setdefault(src, []).append((dst, fed))
                ready[dst] += 1
                continue
            adj[src].append(Link(dst=dst, soft=edge.soft))
            if edge.soft:
                if not has_soft.get(dst, False):  # first soft pred: count once
                    has_soft[dst] = True
                    ready[dst] += 1
            else:
                ready[dst] += 1  # every hard pred counts

        self._adj = adj
        self._initial_ready = ready
        self._err_adj = {src: tuple(routes) for src, routes in err_adj.items()}
        for name, child in self._ops.items():
            child._error_routes = name in err_adj

        # ── Phase 2: stream-context ready counts per generator ────────────────────
        # When generator G emits frame [0], downstream ops run in a new stream ctx,
        # and batch ops (not reachable from G) do not fire there. These counts
        # pre-subtract them. The scheduler no longer seeds item contexts from them
        # (it counts each batch op's actual arrival, `Scheduler._join_edges`, since
        # a batch op may still be running when the first item arrives); they stay
        # because `serialize()` emits them and fingerprints hash that output.
        stream_initial = {}
        for gen_name, gen_op in self._ops.items():
            if not gen_op.is_gen:
                continue
            # Compute ops reachable (downstream) from this generator via BFS.
            gen_reachable: set = set()
            stack = [gen_name]
            while stack:
                node = stack.pop()
                if node in gen_reachable:
                    continue
                gen_reachable.add(node)
                for lnk in self._adj.get(node, []):
                    stack.append(lnk.dst)
                for dst, _fed in err_adj.get(node, ()):
                    stack.append(dst)

            ri = {}
            has_predecrement = False
            for op_name, base_count in ready.items():
                r = base_count
                for pred in self.prevs.get(op_name, []):
                    if pred == gen_name:
                        continue  # gen itself contributes per-frame — don't subtract
                    edge = self._edges.get((pred, op_name))
                    if edge and not edge.soft and pred not in gen_reachable:
                        # pred is a batch op outside gen's chain — already done at root
                        r = max(0, r - 1)
                        if op_name != gen_name:
                            has_predecrement = True
                ri[op_name] = r
            if has_predecrement:
                stream_initial[gen_name] = ri
        self._stream_initial_ready = stream_initial

    def _setup_schema(self):
        """Discover inputs/outputs from child ops.

        Scans child ops for Ref references pointing to PARENT (self) —
        those become the graph's inputs/outputs. After a Phase 3 cycle
        rewrite the SCC ops live inside a synthetic hidden loop but their
        PARENT refs still point at *this* outer graph, so we descend into
        synthetic-loop children when collecting refs (BUG 8 fix — otherwise
        outer.inputs silently loses the moved ops' PARENT contributions
        and Operon.input_schema() returns an incomplete surface).
        """
        LOGGER.debug("Graph [highlight]%s[/highlight]: building schema...", self.name)
        graph_inputs = {}
        graph_outputs = {}
        self._output_writers = {}

        def _collect(child_name: str, child, loop_owner=None):
            """Recurse into synthetic loops when scanning for PARENT refs.

            ``loop_owner`` (when set) is the nearest enclosing synthetic
            hidden loop containing ``child``. Its ``_out_vars`` gets the
            emission mapping — that way the loop's own scheduler emits per-op
            frames for moved SCC ops during ``engine.stream(mode="updates")``
            (BUG 7 fix). ``self.inputs``/``self.outputs`` still absorb the
            declaration so the outer graph's schema surface is complete
            (BUG 8 fix).
            """
            for var, param in child.inputs.items():
                if isinstance(param.value, Ref) and param.value.raw_source is self:
                    graph_inputs[param.value.var] = Param(
                        type=param.type,
                        required=param.required,
                        default=param.default,
                        description=param.description,
                    )

            for var, param in child.outputs.items():
                if isinstance(param.value, Ref) and param.value.raw_source is self:
                    graph_outputs[param.value.var] = Param(
                        type=param.type,
                        required=param.required,
                        default=param.default,
                        description=param.description,
                    )
                    (loop_owner or self)._out_vars.setdefault(child_name, {})[var] = param.value.var
                    self._output_writers.setdefault(param.value.var, []).append(child)

            # Descend into synthetic hidden loops — their children's PARENT
            # refs point through the loop back to us.
            if getattr(child, "_synthetic", False):
                for grand_name, grand in child._ops.items():
                    _collect(grand_name, grand, loop_owner=child)

        for child_name, child in self._ops.items():
            _collect(child_name, child)

        self.inputs = self._merge_params(graph_inputs, self.inputs)
        self.outputs = self._merge_params(graph_outputs, self.outputs)

        # Validate: Refs in child op inputs must point to ops inside this graph
        # or to PARENT (self). Refs to ops in a parent graph won't resolve at
        # runtime because the child runs in its own state.
        self._validate_ref_scope()

    def _setup_endpoints(self):
        """Discover entry/exit ops from the graph topology."""
        LOGGER.debug("Graph [highlight]%s[/highlight]: setting up endpoints...", self.name)

        if not self.entries:
            self.entries = [name for name in self._ops if not self.prevs[name]]

        if not self.exits:
            self.exits = [name for name in self._ops if not self.nexts[name]]

        if not self.entries:
            LOGGER.error(
                "Graph [highlight]%s[/highlight]: no entry op found. Check START >> op connections.",
                self.name,
            )
            raise ValueError("Graph must have at least one entry op.")
        if not self.exits:
            LOGGER.error(
                "Graph [highlight]%s[/highlight]: no exit op found. Check op >> END connections.",
                self.name,
            )
            raise ValueError("Graph must have at least one exit op.")

    def _validate_ref_scope(self):
        """Validate that all Ref inputs in child ops point to ops inside this graph,
        PARENT, an ancestor GraphOp, or an op inside a synthetic-loop descendant
        (Phase 3: outer ops may hold refs to SCC ops that were moved into a
        hidden loop; the underlying op instance is unchanged — only its parent
        graph is different — so state lookups still resolve correctly).

        A Ref pointing to an op in a *sibling* subgraph (a graph elsewhere in the
        tree that isn't an ancestor or a synthetic descendant) won't resolve at
        runtime because that graph runs in its own isolated state — those still
        error.

        Raises:
            ValueError: If a Ref points to an op outside the allowed scope.
        """
        valid_sources = set(self._ops.keys())

        # Walk the parent chain to collect ancestor GraphOps (identity-based).
        ancestors: set = set()
        cur = self.parent
        while cur is not None and hasattr(cur, "_ops"):
            ancestors.add(id(cur))
            cur = getattr(cur, "parent", None)

        # Collect descendant op identities inside synthetic hidden loops (BUG 3
        # from Phase 3 review — the rewrite moves SCC ops into a synthetic loop
        # but outer ops may still hold refs to them; the op instances are
        # unchanged so state lookups by full_name work).
        descendants: set = set()

        def _walk_synthetic(node):
            for child in node._ops.values():
                if getattr(child, "_synthetic", False):
                    for grand in child._ops.values():
                        descendants.add(id(grand))
                    _walk_synthetic(child)

        _walk_synthetic(self)

        for child_name, child in self._ops.items():
            for var, param in child.inputs.items():
                if not isinstance(param.value, Ref):
                    continue
                ref: Ref = param.value
                # PARENT refs pointing to self are OK — resolve from graph inputs
                if ref.raw_source is self:
                    continue
                # Refs to ops inside this graph are OK
                source_name = getattr(ref.raw_source, "name", None)
                if source_name in valid_sources:
                    continue
                # Refs to any ancestor GraphOp are OK
                if id(ref.raw_source) in ancestors:
                    continue
                # Refs to any synthetic-loop descendant op are OK
                if id(ref.raw_source) in descendants:
                    continue
                # Ref points to an op outside this graph and outside the ancestor
                # / synthetic-descendant chain → error.
                source_repr = source_name or repr(ref.raw_source)
                raise ValueError(
                    f"Graph '{self.name}': op '{child_name}' input '{var}' references "
                    f"'{source_repr}' which is outside this graph's scope. "
                    f"Pass the value through PARENT instead: "
                    f"inputs={{'{var}': PARENT['{var}']}} and provide '{var}' as a graph input."
                )

    def _cache_full_names(self) -> None:
        """Cache full_name for this op and all descendants after build."""
        self._cache_full_name()
        for child in self._ops.values():
            child._cache_full_name()
            if hasattr(child, "_cache_full_names"):
                child._cache_full_names()

    # ═══════════════════════════════════════════════════════════════════
    # 3. EXECUTE — run the workflow
    # ═══════════════════════════════════════════════════════════════════

    def _seed_declared_inputs(self, state: "MemoryState", context_id: tuple) -> None:
        """Start a declared cell from the input of the same name.

        An agent declares ``PARENT.declare(messages=[...])`` and also takes
        ``messages`` as its input: one cell, both roles. Run on its own, the
        engine writes the inputs into it before the first op. Nested, the
        input arrives as a pull ref — and a shared cell is never pulled, so
        the parent's value never entered it: the agent ran without the
        question it was asked. Writing it here, through the reducer, gives
        the nested run the same starting cell as the standalone one.
        """
        schema = state.schema
        for var in self._shared_vars:
            idx = schema.get_index(self.full_name, var)
            pull = schema.get_pull_ref(idx) if idx >= 0 else None
            if pull is None or pull.is_output or pull.idx < 0:
                continue
            value = pull._fn(state._cells[pull.idx][context_id])
            if value is not None:
                state._write_cell(idx, context_id, value)

    def _failed_descendants(self, state: "MemoryState", context_id: tuple) -> List[str]:
        """Full names of the ops under this graph that raised in this run.

        Read from each op's ``error`` cell at ``context_id`` or a context
        below it (a loop iteration, a stream item), so a second run of
        the same graph — the next item of a stream — sees its own
        failures. ``$errors`` keeps only the first failure of each op and
        cannot answer that. Only called when every output is ``None``,
        which a run that succeeded rarely produces, so the walk costs
        nothing on the path that matters.
        """
        schema = state.schema
        cells = state._cells
        n = len(context_id)
        failed: List[str] = []
        stack = list(self._ops.values())
        while stack:
            child = stack.pop()
            stack.extend(getattr(child, "_ops", {}).values())
            idx = schema.get_index(child.full_name, "error")
            if idx < 0:
                continue
            for ctx, err in cells[idx].items():
                if err is not None and ctx[:n] == context_id:
                    failed.append(child.full_name)
                    break
        return sorted(failed)

    def _drop_unwritten(
        self, state: "MemoryState", context_id: tuple, outputs: Dict[str, Any]
    ) -> None:
        """Set to ``None`` each output no op under this graph wrote in this run.

        An output named like one of the graph's inputs shares that input's
        cell. When the op writing it raised, or never ran because an op
        before it raised, the cell still holds what came in — the outputs
        are not all ``None``, and the next op ran on the graph's own input
        as if it were its answer. An output counts as written when one of
        its writers finished without an error at ``context_id`` or a
        context below it: every op run writes its ``error`` cell, ``None``
        when it succeeded, so a key there holding ``None`` is a successful
        run. Declared cells are left alone: they hold the graph's state
        across writes, not a single answer. Only called when some op in the
        run raised, so a run without failures pays nothing.
        """
        schema = state.schema
        cells = state._cells
        n = len(context_id)

        def wrote(op) -> bool:
            idx = schema.get_index(op.full_name, "error")
            return idx >= 0 and any(
                err is None and ctx[:n] == context_id for ctx, err in cells[idx].items()
            )

        for var, writers in self._output_writers.items():
            if outputs.get(var) is None or var in self._shared_vars:
                continue
            if not any(wrote(w) for w in writers):
                outputs[var] = None

    @staticmethod
    def _subgraph_error(failed: List[str]) -> str:
        """The ``$errors`` text of a subgraph whose ops raised."""
        names = ", ".join(repr(name) for name in failed)
        return (
            f"SubgraphError: {names} raised, so this subgraph produced no output "
            f"and the ops after it did not run. The error is under that name."
        )

    def store_result(self, state: "MemoryState", result: Dict[str, Any], context_id: str) -> None:
        """Store the graph's outputs — except its declared cells.

        A declared cell already holds the graph's value: every write inside
        the graph went into it. Storing the graph's output there again hands
        the cell's reducer its own accumulated value as a fresh delta — an
        ``add_messages`` cell came out with every message twice.
        """
        if self._shared_vars and result:
            result = {k: v for k, v in result.items() if k not in self._shared_vars}
        super().store_result(state, result, context_id)

    async def run(
        self,
        state: "MemoryState",
        context_id: Optional[tuple] = None,
    ) -> AsyncGenerator[Tuple[tuple, Dict[str, Any]], None]:
        """Execute graph: get inputs → schedule ops → loop if needed → store results."""

        # `BaseOp.run` honours this and `GraphOp` overrides `run`, so without
        # the same check here `enabled=False` silenced a subgraph's *output*
        # while its children still ran — every op inside it, LLM calls and
        # all. A consumer switching a stage off to save the spend kept
        # paying for it and had no way to tell.
        if context_id is None:
            context_id = DEFAULT_CONTEXT

        if not self.enabled:
            # Same contract as `BaseOp.run`: completed, produced nothing.
            # Returning without a yield leaves every successor waiting
            # forever — see the note there.
            yield context_id, {}
            return

        request_id = state.request_id
        start_time = datetime.now(timezone.utc)
        perf_start = perf_counter()
        _inputs = {}
        _outputs = {}
        error_msg = None

        try:
            _inputs = self.get_inputs(state, context_id=context_id)
            self._seed_declared_inputs(state, context_id)

            if self._is_building:
                self.build()

            if self._policy is None:
                _outputs, stream_ctxs, _interrupted = await self._scheduler.run(state, context_id)
            else:
                # The deadline cancels the nested scheduler, whose `finally`
                # cancels the op tasks it started; the TimeoutError is then
                # this graph's failure, recorded below like any other.
                run_s = self._policy.timeout.run
                async with _Deadline(
                    asyncio.get_running_loop().time() + run_s,
                    f"{self.full_name} ran past Timeout(run={run_s:g}) and was cancelled",
                ):
                    _outputs, stream_ctxs, _interrupted = await self._scheduler.run(
                        state, context_id
                    )

            # A graph with a generator hands its parent one output per stream
            # context. A run in which no stream ran — a branch went around it —
            # hands on its one output at this context, like a graph without
            # one, provided its last ops wrote it. A generator that yielded
            # nothing wrote no output: it still hands on nothing.
            _streamed = bool(stream_ctxs) or (
                any(op.is_gen for op in self._ops.values())
                and all(v is None for v in _outputs.values())
            )
            if _interrupted:
                # The invocation was cancelled from inside. `_outputs` is
                # whatever the cells happened to hold — all-`None` when the
                # cancelled op never wrote — and yielding it hands the parent
                # a result indistinguishable from a successful null answer.
                # The streaming branch below already skips all-`None` items;
                # this is the batch equivalent.
                _outputs = {}
            elif not _streamed:
                if state._op_errors:
                    self._drop_unwritten(state, context_id, _outputs)
                failed = (
                    self._failed_descendants(state, context_id)
                    if all(v is None for v in _outputs.values())
                    else ()
                )
                if failed:
                    # An op under this graph raised and nothing was written:
                    # this run failed. Yielding the all-`None` outputs ran
                    # the next op on them, where flat the failing op's
                    # successor never runs — and a door answered `200 null`.
                    # Not yielding is the flat rule; the streaming branch
                    # below already skips all-`None` items.
                    _outputs = {}
                    if not getattr(self, "_synthetic", False):
                        # A synthetic loop is not an op its author wrote;
                        # the graph around it reports the failure.
                        error_msg = self._subgraph_error(failed)
                        state.record_op_error(self.full_name, error_msg, context_id)
                        if self._error_routes:
                            yield (
                                context_id,
                                Failure(self.name, context_id, error_msg, dict(_inputs)),
                            )
                else:
                    self.store_result(state, _outputs, context_id)
                    yield context_id, _outputs
            else:
                for sctx in stream_ctxs:
                    item = self.get_outputs(state, context_id=sctx)
                    if state._op_errors:
                        self._drop_unwritten(state, sctx, item)
                    if any(v is not None for v in item.values()):
                        self.store_result(state, item, sctx)
                        yield sctx, item

        except Exception:
            import sys

            error_msg = (
                traceback.format_exc()
                if LOGGER.isEnabledFor(40)
                else f"{type(sys.exc_info()[1]).__name__}: {sys.exc_info()[1]}"
            )
            LOGGER.error(
                "[title]\\[%s][/title] Error in op [highlight]%s[/highlight]:\n%s",
                request_id,
                self.name,
                error_msg.rstrip(),
            )
            # A child's failure is caught in the child's own `BaseOp.run`;
            # this is the subgraph failing around its children. Same record.
            state.record_op_error(self.full_name, sys.exc_info()[1], context_id)
            if self._error_routes:
                failed = sys.exc_info()[1]
                yield (
                    context_id,
                    Failure(
                        self.name, context_id, f"{type(failed).__name__}: {failed}", dict(_inputs)
                    ),
                )
            else:
                run_policy = state._run_policy
                if run_policy is not None and run_policy.fail_fast:
                    raise fail_fast(state, self.full_name, sys.exc_info()[1])

        finally:
            end_time = datetime.now(timezone.utc)
            duration_ms = (perf_counter() - perf_start) * 1000
            self._log(request_id, context_id, _inputs, _outputs, duration_ms)
            self._store_metrics(
                state,
                context_id,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
            )
            if error_msg is not None:
                state[self.full_name, "error", context_id] = error_msg

    # ═══════════════════════════════════════════════════════════════════
    # 4. EXPORT — serialization, validation, debug
    # ═══════════════════════════════════════════════════════════════════

    def serialize(self) -> dict:
        """The whole graph as a config dict: ops, edges, entries and exits.

        A graph's canonical description; an eval's fingerprint hashes it.
        It was written for the dropped Rust runtime, and some keys are
        still that runtime's (``initial_ready_count``, ``compiled_adj``,
        ``stream_initial_ready``): kept so the output, and every hash
        taken of it, stays the same.
        """
        # Phase 3: synthetic hidden loops can't serialize as classic
        # loop_config because their termination is scheduler-side (back-edge
        # activation) with no equivalent until-expression. Emitting a
        # classic loop_config would silently tell external consumers "iter
        # to max_iterations with no exit condition" (HAZARD from Phase 3
        # review). Refuse loudly instead — callers must recompile from
        # source for now.
        if getattr(self, "_loop_mode", None) == "synthetic":
            raise NotImplementedError(
                f"GraphOp '{self.name}' is a synthetic loop from the Phase 3 "
                "cycle-rewrite pass and does not yet have a serialization "
                "format. Serialize the pre-rewrite graph or use "
                "@graph(strict_dag=True) to opt out."
            )

        # Also refuse to serialize an outer graph that contains any synthetic
        # loop descendant — the missing sub-config would poison consumers.
        def _contains_synthetic(node):
            for child in node._ops.values():
                if getattr(child, "_synthetic", False):
                    return True
                if hasattr(child, "_ops") and _contains_synthetic(child):
                    return True
            return False

        if _contains_synthetic(self):
            raise NotImplementedError(
                f"GraphOp '{self.name}' contains a synthetic hidden loop "
                "(Phase 3 cycle-rewrite output). Serialize the pre-rewrite "
                "graph or use @graph(strict_dag=True) on the affected subgraph."
            )

        base = super().serialize()
        base.update(
            {
                "ops": {name: op.serialize() for name, op in self._ops.items()},
                # An error edge says so; no other edge changes, and neither
                # does any fingerprint hashed from a graph without one.
                "edges": [
                    {"from": src, "to": dst, "soft": edge.soft}
                    | ({"kind": "error"} if edge.type == "error" else {})
                    for (src, dst), edge in self._edges.items()
                ],
                "entries": list(self.entries),
                "exits": list(self.exits),
                "initial_ready_count": dict(self._initial_ready),
                "compiled_adj": {
                    op: [[link.dst, link.soft] for link in links] for op, links in self._adj.items()
                },
                "stream_initial_ready": self._stream_initial_ready,
                # Only synthetic loops carry a loop config, and they refuse
                # to serialize above, so this is always None today.
                "loop_config": {"max_iterations": self._loop_config.max_iterations}
                if self._loop_config
                else None,
                "max_stream_concurrent": self.concurrency,
            }
        )
        return base

    def validate(self) -> ValidationResult:
        """Run all validations and return result."""
        # Collect ancestor GraphOp names so _validate_refs allows moved-SCC
        # ops to keep their outer PARENT refs after Phase 3 rewrite.
        ancestor_names: set = set()
        cur = self.parent
        while cur is not None and hasattr(cur, "_ops"):
            if getattr(cur, "name", None):
                ancestor_names.add(cur.name)
            cur = getattr(cur, "parent", None)

        # Collect descendant op names inside synthetic hidden loops so
        # _validate_refs allows outer ops to reference moved SCC ops (BUG 3).
        descendant_names: set = set()

        def _walk(node):
            for child in node._ops.values():
                if getattr(child, "_synthetic", False):
                    for grand_name in child._ops:
                        descendant_names.add(grand_name)
                    _walk(child)

        _walk(self)

        # Collect sibling op names — ops living in any ancestor's _ops (not
        # including ancestor graphs themselves). A BranchOp moved into a
        # synthetic hidden loop may reference siblings that stayed at the
        # outer level (BUG 2 / E3 multi-exit-via-branch): the branch's
        # __branch_target__ is re-routed through the loop's outgoing edges
        # at the outer level.
        sibling_names: set = set()
        cur = self.parent
        while cur is not None and hasattr(cur, "_ops"):
            for name in cur._ops:
                if name != self.name:
                    sibling_names.add(name)
            cur = getattr(cur, "parent", None)

        return validate_graph(
            self.name,
            self._ops,
            self._edges,
            self.prevs,
            self.nexts,
            self.entries,
            self.exits,
            ancestor_names=ancestor_names,
            descendant_names=descendant_names,
            sibling_names=sibling_names,
            graph=self,
            arm_sig=self._arm_signatures(),
        )

    def show(self, indent=0):
        """Display graph structure (debug)."""
        prefix = "  " * indent
        LOGGER.debug("%sGraph: %s", prefix, self.name)
        LOGGER.debug("%sOps: %s", prefix, list(self._ops.keys()))
        LOGGER.debug("%sEdges:", prefix)
        for edge in self._edges.values():
            soft_marker = " (soft)" if edge.soft else ""
            LOGGER.debug(
                "%s  %s -> %s: %s%s", prefix, edge.from_node, edge.to_node, edge.type, soft_marker
            )
        LOGGER.debug("%sReady count: %s", prefix, dict(self._initial_ready))

        for child in self._ops.values():
            if isinstance(child, GraphOp):
                child.show(indent + 1)


from operonx.core.ops.graph._decorators import graph  # noqa: E402, F401
