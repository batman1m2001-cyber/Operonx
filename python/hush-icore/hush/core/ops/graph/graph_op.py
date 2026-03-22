"""GraphOp — container op that manages a graph of child ops.

Package layout::

    graph_op.py      GraphOp class (define + build + run + export)
    scheduler.py     run_scheduler() — see its docstring for the full execution story
    _loop.py         LoopConfig + run_loop() for feedback loops
    _decorators.py   @graph and @graph.loop decorators
    validation.py    Graph validation rules and error types
"""

import traceback
from collections import defaultdict
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable, Dict, Optional, Union

from hush.core.configs.edge_config import EdgeConfig, EdgeType
from hush.core.configs.op_config import OpType
from hush.core.loggings import LOGGER
from hush.core.ops.base import END, PARENT, START, BaseOp
from hush.core.ops.graph._loop import LoopConfig, run_loop
from hush.core.ops.graph.scheduler import _is_gen, run_scheduler

# Re-export validation types for backward compatibility
from hush.core.ops.graph.validation import (  # noqa: E402, F401
    GraphValidationError,
    ValidationIssue,
    ValidationLevel,
    ValidationResult,
    validate_graph,
)
from hush.core.states import MemoryState, Ref
from hush.core.states.cell import DEFAULT_CONTEXT
from hush.core.utils.common import Param
from hush.core.utils.context import _current_graph


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
                         _setup_schema        scan PARENT refs → graph inputs/outputs
                         _setup_endpoints     find entry/exit ops from topology
                         _build_ready_counts  count predecessors per op
                         _build_adj           compile adjacency list
                         _build_predecrements streaming: pre-compute batch adjustments
                         validate             branch targets, cycles, reachability, refs

        3. EXECUTE       g.run(state, context_id, parent_context)
                         → run_scheduler()   drain_ready → dispatch_op → event loop
                                             (propagate completion, collect outputs)
                                             see scheduler.py for full flow
                         → run_loop()        if loop config, iterate until condition met

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
        "initial_ready_count",
        "has_soft_preds",
        "_edges",
        "_is_building",
        "_compiled_adj",
        "_stream_predecrements",
        "concurrency",
        "_loop_config",
        "stream_contexts",
    ]

    type: OpType = "graph"

    # ═══════════════════════════════════════════════════════════════════
    # 1. DEFINE — build the graph structure
    # ═══════════════════════════════════════════════════════════════════

    def __init__(self, concurrency: int = 64, **kwargs):
        super().__init__(**kwargs)
        self._token = None
        self._is_building = True
        self._ops: Dict[str, BaseOp] = {}
        self._edges = {}
        self.entries = []
        self.exits = []
        self.prevs = defaultdict(list)
        self.nexts = defaultdict(list)
        self.has_soft_preds = set()
        self._stream_predecrements = {}
        self.concurrency = concurrency
        self._compiled_adj = {}
        self._loop_config = None
        self.stream_contexts = []

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
        """Create a GraphOp configured for feedback-loop execution.

        Each iteration re-runs the graph's scheduler, carrying forward outputs
        as the next iteration's inputs. Stops when ``until`` evaluates to True
        or ``max_iterations`` is reached.

        Args:
            name: Graph name.
            until: Stop condition — a string expression (evaluated against outputs)
                   or a callable ``(outputs_dict) -> bool``.
            max_iterations: Safety cap on iterations (default 100).
            **initial_state: Initial values for loop variables, injected as inputs.

        Example::

            with GraphOp.loop(name="counter", until="count >= 5", count=0) as g:
                inc = increment(counter=PARENT["count"])
                inc["counter"] >> PARENT["count"]
                START >> inc >> END
        """
        g = cls(name=name, inputs=initial_state or None)
        g._loop_config = LoopConfig(
            until=until,
            max_iterations=max_iterations,
            initial_state=initial_state,
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

        if getattr(op, "_is_hush_builder", False):
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

    def add_edge(self, source: str, target: str, type: EdgeType = "normal", soft: bool = False):
        """Add an edge between two ops.

        Args:
            source: Source op name.
            target: Target op name.
            type: Edge type (normal, lookback, condition).
            soft: If True, edge does not count toward ready_count.
                  Used for branch outputs when only one branch executes.
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

        new_edge = EdgeConfig(from_node=source, to_node=target, type=type, soft=soft)
        if (source, target) not in self._edges:
            self._edges[source, target] = new_edge
            self.nexts[source].append(target)
            self.prevs[target].append(source)

    # ═══════════════════════════════════════════════════════════════════
    # 2. BUILD — compile graph for execution
    # ═══════════════════════════════════════════════════════════════════

    def build(self):
        """Build graph: children first, then schema → endpoints → topology → validation."""
        for child in self._ops.values():
            if hasattr(child, "build"):
                child.build()

        self._setup_schema()
        self._setup_endpoints()
        self._build_ready_counts()

        result = self.validate()
        result.raise_if_errors()

        self._build_adj()
        self._build_predecrements()

        if self._loop_config and isinstance(self._loop_config.until, str):
            self._loop_config._compiled_until = compile(self._loop_config.until, "<until>", "eval")

        self._is_building = False
        self._cache_full_names()

    def _setup_schema(self):
        """Discover inputs/outputs from child ops.

        Scans child ops for Ref references pointing to PARENT (self) —
        those become the graph's inputs/outputs.
        """
        LOGGER.debug("Graph [highlight]%s[/highlight]: building schema...", self.name)
        graph_inputs = {}
        graph_outputs = {}

        for _, child in self._ops.items():
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

        self.inputs = self._merge_params(graph_inputs, self.inputs)
        self.outputs = self._merge_params(graph_outputs, self.outputs)

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

    def _build_ready_counts(self):
        """Compute initial_ready_count from edge topology.

        - Hard edge (>>) counts individually
        - Soft edges (>) to the same target count as 1 (wait for ANY soft pred)
        - Example: A >> D, B > D, C > D => initial_ready_count[D] = 2 (1 hard + 1 soft group)
        """
        self.initial_ready_count = {}
        self.has_soft_preds = set()
        for name in self._ops:
            hard_pred_count = 0
            has_soft = False
            for pred in self.prevs[name]:
                edge = self._edges.get((pred, name))
                if edge and edge.soft:
                    has_soft = True
                else:
                    hard_pred_count += 1
            if has_soft:
                self.has_soft_preds.add(name)
                hard_pred_count += 1
            self.initial_ready_count[name] = hard_pred_count

    def _build_adj(self):
        """Compile adjacency list for faster traversal at runtime."""
        self._compiled_adj = {}
        for name in self._ops:
            adj = []
            for successor in self.nexts[name]:
                edge = self._edges.get((name, successor))
                is_soft = bool(edge and edge.soft)
                adj.append((successor, is_soft))
            self._compiled_adj[name] = adj

    def _build_predecrements(self):
        """Pre-compute ready_count predecrements per generator.

        When a generator yields and creates context [n], batch-level
        predecessors of downstream ops have already completed. Their
        ready_count contributions must be pre-decremented.
        """
        self._stream_predecrements = {}

        gen_names = {name for name, op in self._ops.items() if _is_gen(op)}
        if not gen_names:
            return

        for gen_name in gen_names:
            reachable = set()
            queue = [gen_name]
            while queue:
                current = queue.pop(0)
                for succ in self.nexts.get(current, []):
                    if succ not in reachable:
                        reachable.add(succ)
                        queue.append(succ)

            predecrements = {}
            for succ_name in reachable:
                decrement = 0
                for pred in self.prevs.get(succ_name, []):
                    if pred == gen_name:
                        continue
                    if pred not in reachable:
                        edge = self._edges.get((pred, succ_name))
                        if edge:
                            decrement += 1
                if decrement > 0:
                    predecrements[succ_name] = decrement
            if predecrements:
                self._stream_predecrements[gen_name] = predecrements

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
        context_id: Optional[str] = None,
        parent_context: Optional[str] = None,
    ) -> Dict[str, Any]:
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
            _inputs = self.get_inputs(state, context_id=context_id, parent_context=parent_context)

            if self._is_building:
                self.build()

            _outputs, stream_ctxs = await run_scheduler(
                self, state, context_id, parent_context, request_id
            )
            self.stream_contexts = stream_ctxs

            if self._loop_config:
                _outputs = await run_loop(
                    self, state, context_id, parent_context, request_id, _outputs
                )

            # Pop _loop_metrics before store_result (not a schema variable)
            loop_metrics = _outputs.pop("_loop_metrics", None)
            self.store_result(state, _outputs, context_id)
            if loop_metrics is not None:
                _outputs["_loop_metrics"] = loop_metrics

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

            return _outputs

    # ═══════════════════════════════════════════════════════════════════
    # 4. EXPORT — serialization, validation, debug
    # ═══════════════════════════════════════════════════════════════════

    def serialize(self) -> dict:
        """Serialize full graph to config dict for the Rust backend."""
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
                "initial_ready_count": dict(self.initial_ready_count),
                "has_soft_preds": list(self.has_soft_preds),
                "compiled_adj": {
                    op: [[succ, soft] for succ, soft in successors]
                    for op, successors in self._compiled_adj.items()
                },
                "stream_predecrements": self._stream_predecrements,
                "loop_config": {
                    "until": self._loop_config.until
                    if isinstance(self._loop_config.until, str)
                    else None,
                    "max_iterations": self._loop_config.max_iterations,
                    "loop_vars": list(self._loop_config.initial_state.keys()),
                }
                if self._loop_config
                else None,
                "max_stream_concurrent": self.concurrency,
            }
        )
        return base

    def validate(self) -> ValidationResult:
        """Run all validations and return result."""
        return validate_graph(
            self.name,
            self._ops,
            self._edges,
            self.prevs,
            self.nexts,
            self.entries,
            self.exits,
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
        LOGGER.debug("%sReady count: %s", prefix, dict(self.initial_ready_count))

        for child in self._ops.values():
            if isinstance(child, GraphOp):
                child.show(indent + 1)


from hush.core.ops.graph._decorators import graph  # noqa: E402, F401
