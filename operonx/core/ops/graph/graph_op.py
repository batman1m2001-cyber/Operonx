"""GraphOp — container op that manages a graph of child ops.

Package layout::

    graph_op.py      GraphOp class (define + build + run + export)
    task_scheduler.py LoopConfig, Scheduler
    _decorators.py   @graph and @graph.loop decorators
    validation.py    Graph validation rules and error types
"""

import traceback
from collections import defaultdict
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, AsyncGenerator, Callable, Dict, NamedTuple, Optional, Tuple, Union

from operonx.core.configs.edge_config import EdgeConfig, EdgeType
from operonx.core.configs.op_config import OpType
from operonx.core.loggings import LOGGER
from operonx.core.ops.base import END, PARENT, START, BaseOp
from operonx.core.ops.graph.task_scheduler import (
    LoopConfig,
    Scheduler,
)

# Re-export validation types for backward compatibility
from operonx.core.ops.graph.validation import (  # noqa: E402, F401
    GraphValidationError,
    ValidationIssue,
    ValidationLevel,
    ValidationResult,
    validate_graph,
)
from operonx.core.states import MemoryState, Ref
from operonx.core.states.cell import DEFAULT_CONTEXT
from operonx.core.utils.common import Param
from operonx.core.utils.context import _current_graph


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

        4. EXPORT        serialize()  config dict for Rust backend
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
        "_adj",
        "_initial_ready",
        "_stream_initial_ready",
        "_scheduler",
        "_out_vars",
        "_auto_soft",
        # Phase 3 cycle-rewrite plumbing
        "_strict_dag",
        "_synthetic",
        "_loop_mode",
        "_back_edge_sources",
        "_back_edges",
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
        self._adj = {}  # {op_name: [Link(dst, soft), ...]}
        self._initial_ready = {}  # {op_name: ready_count}
        self._stream_initial_ready = {}  # {gen_name: {op_name: ready_count_for_stream_ctx}}
        self._scheduler = None  # set by build()
        self._out_vars: Dict[
            str, dict
        ] = {}  # {op_name: {src_var: dest_var}} — vars mapped to PARENT output
        self._auto_soft = auto_soft  # auto-soften branch-merge edges at build time
        # Phase 3: opt-out for the Level-2 cycle→loop rewrite. When True, back-edges
        # remain as-is and hit the classic validate() warning path.
        self._strict_dag = strict_dag
        # Phase 3: hidden loops created by the cycle rewrite carry these markers.
        self._synthetic = False
        self._loop_mode = None  # None (classic), "synthetic" (rewritten hidden loop)
        self._back_edge_sources: set = set()  # audit only; termination consults _back_edges
        self._back_edges: list = []  # List[(u_name, v_name)] for termination per back-edge
        self._rewritten_from = None  # audit dict populated by rewrite_cycles_to_loops

    def __enter__(self):
        """Enter context manager mode — ops created inside are auto-registered."""
        self._token = _current_graph.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager mode — discover schema on clean exit."""
        _current_graph.reset(self._token)
        if exc_type is None:
            self._setup_schema()

    @classmethod
    def loop(
        cls,
        name: Optional[str] = None,
        until: Optional[Union[str, Callable]] = None,
        max_iterations: int = 100,
        **initial_state: Any,
    ):
        """DEPRECATED (Phase 3): construct a GraphOp configured for feedback-loop
        execution.

        Prefer writing the loop directly with a back-edge — the Phase 3 build-time
        rewrite pass synthesizes an equivalent hidden loop for you::

            @graph
            def counter():
                inc = increment(counter=PARENT["count"])
                inc["counter"] >> PARENT["count"]
                START >> inc >> if_(PARENT["count"] >= 5, END).else_(inc)

        Each iteration re-runs the graph's scheduler, carrying forward outputs
        as the next iteration's inputs. Stops when ``until`` evaluates to True
        or ``max_iterations`` is reached.

        This constructor still works but emits a :class:`DeprecationWarning`. The
        rewrite pass calls this internally via ``_internal=True`` to avoid noise.

        Args:
            name: Graph name.
            until: Stop condition — a string expression (evaluated against outputs)
                   or a callable ``(outputs_dict) -> bool``.
            max_iterations: Safety cap on iterations (default 100).
            **initial_state: Initial values for loop variables, injected as inputs.
        """
        if not initial_state.pop("_internal", False):
            import warnings

            warnings.warn(
                "GraphOp.loop() is deprecated; write the loop with a back-edge inside "
                "@graph and let the Phase 3 rewrite pass synthesize the loop.",
                DeprecationWarning,
                stacklevel=2,
            )
        g = cls(name=name, inputs=initial_state or None)
        g._loop_config = LoopConfig(
            until=until,
            max_iterations=max_iterations,
        )
        return g

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
        from operonx.core.ops.graph.cycle_rewrite import rewrite_cycles_to_loops

        rewrite_cycles_to_loops(self)

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
        if self.bound is None:
            if all(getattr(op, "bound", None) == "sync" for op in self._ops.values()):
                self.bound = "sync"
            else:
                self.bound = "io"

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

        Known limitation: does not detect "sneak paths" (a predecessor
        reachable from a non-branch root that bypasses ``B`` entirely). In
        that case, the two predecessors may not truly be mutually exclusive
        and this pass over-softens. Use ``hard=True`` on the specific edge or
        ``auto_soft=False`` on the whole graph to opt out.
        """
        if not self._auto_soft:
            return

        # For each branch op B, precompute the set of ops each of B's direct
        # successors can reach (forward via all edges). This gives us, for any
        # op p, the set of B's first-hop children through which p is reachable.
        branch_names = [n for n, op in self._ops.items() if op.type == "branch"]
        if not branch_names:
            return

        # successor_reachable[branch][successor] = set of ops reachable from successor
        successor_reachable: Dict[str, Dict[str, set]] = {}
        for b in branch_names:
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

        def branch_sig(target_op: str) -> Dict[str, set]:
            """{branch_name: {first_hop_child_of_branch that can reach target_op}}."""
            sig: Dict[str, set] = {}
            for b, succ_reach in successor_reachable.items():
                first_hops = {succ for succ, reach in succ_reach.items() if target_op in reach}
                if first_hops:
                    sig[b] = first_hops
            return sig

        audit_log = []

        for merge_name in list(self._ops.keys()):
            preds = self.prevs.get(merge_name, [])
            if len(preds) < 2:
                continue

            # Signatures per pred (computed against original edge structure).
            sigs = {p: branch_sig(p) for p in preds}

            for p in preds:
                edge = self._edges.get((p, merge_name))
                if edge is None or edge.soft or edge.pinned_hard:
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

        for (src, dst), edge in self._edges.items():
            adj[src].append(Link(dst=dst, soft=edge.soft))
            if edge.soft:
                if not has_soft.get(dst, False):  # first soft pred: count once
                    has_soft[dst] = True
                    ready[dst] += 1
            else:
                ready[dst] += 1  # every hard pred counts

        self._adj = adj
        self._initial_ready = ready

        # ── Phase 2: stream-context ready counts per generator ────────────────────
        # When generator G emits frame [0], downstream ops run in a new stream ctx.
        # Batch ops (not reachable from G) already ran before G started — they will
        # NOT fire EOFs at item contexts, so their ready-count contributions must be
        # pre-subtracted when seeding item-context ready counts.
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
                    (loop_owner or self)._out_vars.setdefault(child_name, {})[var] = (
                        param.value.var
                    )

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

    async def run(
        self,
        state: "MemoryState",
        context_id: Optional[tuple] = None,
    ) -> AsyncGenerator[Tuple[tuple, Dict[str, Any]], None]:
        """Execute graph: get inputs → schedule ops → loop if needed → store results."""

        if context_id is None:
            context_id = DEFAULT_CONTEXT

        request_id = state.request_id
        start_time = datetime.now(timezone.utc)
        perf_start = perf_counter()
        _inputs = {}
        _outputs = {}
        error_msg = None

        try:
            _inputs = self.get_inputs(state, context_id=context_id)

            if self._is_building:
                self.build()

            _outputs, stream_ctxs = await self._scheduler.run(state, context_id)

            _has_generators = any(op.is_gen for op in self._ops.values())
            if not stream_ctxs and not _has_generators:
                self.store_result(state, _outputs, context_id)
                yield context_id, _outputs
            else:
                for sctx in stream_ctxs:
                    item = self.get_outputs(state, context_id=sctx)
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
        """Serialize full graph to config dict for the Rust backend.

        Note: the key ``"initial_ready_count"`` is kept as-is for Rust backend
        compatibility even though the internal Python attribute was renamed to
        ``_initial_ready`` during the scheduler rewrite.
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
                "edges": [
                    {"from": src, "to": dst, "soft": edge.soft}
                    for (src, dst), edge in self._edges.items()
                ],
                "entries": list(self.entries),
                "exits": list(self.exits),
                "initial_ready_count": dict(self._initial_ready),
                "compiled_adj": {
                    op: [[link.dst, link.soft] for link in links] for op, links in self._adj.items()
                },
                "stream_initial_ready": self._stream_initial_ready,
                "loop_config": {
                    "until": self._loop_config.until
                    if isinstance(self._loop_config.until, str)
                    else None,
                    "max_iterations": self._loop_config.max_iterations,
                }
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
