"""GraphOp — container op that manages a graph of child ops."""

import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from time import perf_counter
from typing import Any, Dict, Optional

from hush.core.configs.edge_config import EdgeConfig, EdgeType
from hush.core.configs.op_config import OpType
from hush.core.loggings import LOGGER
from hush.core.ops.base import END, PARENT, START, BaseOp
from hush.core.states import MemoryState, Ref
from hush.core.states.cell import DEFAULT_CONTEXT
from hush.core.utils.common import Param
from hush.core.utils.context import _current_graph

# =============================================================================
# Loop Configuration
# =============================================================================


@dataclass
class LoopConfig:
    """Configuration for GraphOp.loop() feedback loops."""

    until: Any  # str expression or callable
    max_iterations: int
    initial_state: dict
    _compiled_until: Any = field(default=None, repr=False)


from hush.core.utils.algo import topo_sort
from hush.core.ops.graph.scheduler import Scheduler, _is_gen

# Re-export validation types for backward compatibility
from hush.core.ops.graph.validation import (  # noqa: E402, F401
    GraphValidationError,
    ValidationIssue,
    ValidationLevel,
    ValidationResult,
    validate_graph,
)


class GraphOp(BaseOp):
    """Container op that holds and executes a directed graph of child ops.

    Used to organise ops into reusable sub-workflows. Independent branches
    execute in parallel; dependencies are resolved via ready-count scheduling.
    Use as a context manager — ops created inside the ``with`` block are
    automatically registered.

    Inputs:
        Auto-discovered from child ops that reference ``PARENT["key"]``
        in their inputs.

    Outputs:
        Auto-discovered from child ops that write to ``PARENT["key"]``
        in their outputs, or auto-forwarded by ``>> END``.

    Example::

        with GraphOp(name="pipeline") as graph:
            a = double(x=PARENT["x"])
            b = add(a=a["result"], b=PARENT["y"])
            START >> a >> b >> END
    """

    __slots__ = [
        "_token",
        "_ops",
        "entries",
        "exits",
        "prevs",
        "nexts",
        "initial_ready_count",
        "has_soft_preds",  # Set of ops that have soft predecessors
        "_edges",
        "_is_building",
        "_compiled_adj",
        "_has_streaming_ops",
        "_stream_predecrements",
        "_max_stream_concurrent",
        "_loop_config",
        "_scheduler",
    ]

    type: OpType = "graph"

    def __init__(self, **kwargs):
        """Khởi tạo GraphOp."""
        super().__init__(**kwargs)
        self._token = None
        self._is_building = True
        self._ops: Dict[str, BaseOp] = {}
        self._edges = {}
        self.entries = []
        self.exits = []
        self.prevs = defaultdict(list)
        self.nexts = defaultdict(list)
        self.has_soft_preds = set()  # Ops with soft predecessors
        self._has_streaming_ops = False
        self._stream_predecrements = {}
        self._max_stream_concurrent = 64
        self._compiled_adj = {}
        self._loop_config = None
        self._scheduler = None

    def __enter__(self):
        """Enter context manager mode."""
        self._token = _current_graph.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager mode."""
        _current_graph.reset(self._token)
        if exc_type is None:
            self._setup_schema()

    @classmethod
    def loop(cls, name=None, until=None, max_iterations=100, **initial_state):
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

    def _setup_schema(self):
        """Discover inputs/outputs from child ops.

        Scans child ops for Ref references pointing to PARENT (self) —
        those become the graph's inputs/outputs.
        """
        LOGGER.debug("Graph [highlight]%s[/highlight]: building schema...", self.name)
        graph_inputs = {}
        graph_outputs = {}

        for _, child in self._ops.items():
            # Check inputs: if ref points to self (parent), it's a graph input
            for var, param in child.inputs.items():
                if isinstance(param.value, Ref) and param.value.raw_source is self:
                    # PARENT["x"] resolves to parent — this is a graph input
                    graph_inputs[param.value.var] = Param(
                        type=param.type,
                        required=param.required,
                        default=param.default,
                        description=param.description,
                    )

            # Check outputs: if ref points to self (parent), it's a graph output
            for var, param in child.outputs.items():
                if isinstance(param.value, Ref) and param.value.raw_source is self:
                    # PARENT["x"] resolves to parent — this is a graph output
                    graph_outputs[param.value.var] = Param(
                        type=param.type,
                        required=param.required,
                        default=param.default,
                        description=param.description,
                    )

        # Merge with user-provided inputs/outputs (if any)
        self.inputs = self._merge_params(graph_inputs, self.inputs)
        self.outputs = self._merge_params(graph_outputs, self.outputs)

    def build(self):
        """Build graph by building child ops first, then this graph."""
        for child in self._ops.values():
            if hasattr(child, "build"):
                child.build()

        self._setup_schema()
        self._setup_endpoints()
        self._build_ready_counts()

        # Run full validation (errors will raise, warnings will be logged)
        result = self.validate()
        result.raise_if_errors()

        self._build_adj()
        self._build_streaming()

        # Compile loop until expression if needed
        if self._loop_config and isinstance(self._loop_config.until, str):
            self._loop_config._compiled_until = compile(self._loop_config.until, "<until>", "eval")

        self._scheduler = Scheduler(self)
        self._is_building = False
        self._post_build()
        self._cache_full_names()

    def _build_ready_counts(self):
        """Compute initial_ready_count from edge topology.

        - Hard edge (>>) counts individually
        - Soft edges (>) to the same target count as 1 (wait for ANY soft pred to complete)
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
                elif edge and not edge.soft:
                    hard_pred_count += 1
                elif edge is None:
                    # Edge not found in lookup (shouldn't happen, but still count)
                    hard_pred_count += 1
            # Soft edges count as 1 if present
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

    def _build_streaming(self):
        """Compute stream depths via topological order (Kahn's algorithm).

        Detects generator ops, assigns stream depths, and pre-computes
        predecrements for the scheduler's streaming context creation.
        """
        self._stream_depths = {}
        self._has_streaming_ops = False
        self._stream_predecrements = {}

        # Check if any ops are generators
        has_gens = any(_is_gen(op) for op in self._ops.values())
        if not has_gens:
            return

        self._has_streaming_ops = True

        topological_order = topo_sort(list(self._ops.keys()), dict(self.nexts), dict(self.prevs))

        # Compute stream depth for each op
        for name in topological_order:
            max_pred_depth = 0
            for pred in self.prevs[name]:
                pred_depth = self._stream_depths.get(pred, 0)
                if _is_gen(self._ops[pred]):
                    pred_depth += 1
                max_pred_depth = max(max_pred_depth, pred_depth)
            self._stream_depths[name] = max_pred_depth

        # Store stream depths on each child op for get_inputs() access
        for name, op_obj in self._ops.items():
            op_obj._stream_depths = self._stream_depths

        # Pre-compute stream predecrements per generator.
        # When a streaming context is created, batch ops that already
        # completed need their edges pre-decremented. Exclude the
        # generator's own edges — those are handled by _activate_successors.
        for name in self._ops:
            if _is_gen(self._ops[name]):
                predecrements = {}
                for succ_name in self.initial_ready_count:
                    decrement = 0
                    for pred in self.prevs.get(succ_name, []):
                        if pred == name:
                            continue  # generator's own edge handled by _activate_successors
                        if self._stream_depths.get(pred, 0) < self._stream_depths.get(succ_name, 0):
                            edge = self._edges.get((pred, succ_name))
                            if edge:
                                decrement += 1
                    if decrement > 0:
                        predecrements[succ_name] = decrement
                self._stream_predecrements[name] = predecrements

    def _cache_full_names(self) -> None:
        """Cache full_name for this op and all descendants after build."""
        self._cache_full_name()
        for child in self._ops.values():
            child._cache_full_name()
            if hasattr(child, "_cache_full_names"):
                child._cache_full_names()

    def _post_build(self):
        """Hook for subclasses to run after build. Override in subclasses."""
        pass

    # =========================================================================
    # Serialization
    # =========================================================================

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
            }
        )
        return base

    # =========================================================================
    # Validation Methods
    # =========================================================================

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

        # Warn if an op with the same name already exists (will be overwritten)
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
                  Created via the > operator: case_a > merge_op
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

    def _evaluate_until(self, outputs: dict) -> bool:
        """Evaluate the loop's until condition against current outputs."""
        cfg = self._loop_config
        if cfg is None or cfg.until is None:
            return False
        if callable(cfg.until):
            return bool(cfg.until(outputs))
        compiled = cfg._compiled_until or compile(cfg.until, "<until>", "eval")
        return bool(eval(compiled, {"__builtins__": {}}, outputs))  # noqa: S307

    async def _run_loop(self, state, context_id, parent_context, request_id, outputs):
        """Run feedback loop iterations until condition met or max reached.

        Args:
            state: Workflow state.
            context_id: Base context for the loop.
            parent_context: Context of PARENT, passed to child ops.
            request_id: Request identifier for logging.
            outputs: Initial outputs from the first scheduler pass.

        Returns:
            Final outputs dict with _loop_metrics added.
        """
        iteration = 0
        while True:
            if self._evaluate_until(outputs):
                outputs["_loop_metrics"] = {
                    "total_iterations": iteration + 1,
                    "stopped_by_condition": True,
                }
                return outputs
            iteration += 1
            if iteration >= self._loop_config.max_iterations:
                outputs["_loop_metrics"] = {
                    "total_iterations": iteration,
                    "stopped_by_condition": False,
                    "max_iterations_reached": True,
                }
                return outputs
            # Carry forward outputs as next iteration's inputs
            next_ctx = context_id + (f"loop_{iteration}",)
            for var_name, value in outputs.items():
                state[self.full_name, var_name, next_ctx] = value
            outputs, _ = await self._scheduler.run(state, next_ctx, parent_context, request_id)

    async def run(
        self,
        state: "MemoryState",
        context_id: Optional[str] = None,
        parent_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute graph by running all ops in dependency order.

        Args:
            state: Workflow state.
            context_id: Context of this graph.
            parent_context: Context of PARENT, passed to child ops.
        """

        if context_id is None:
            context_id = DEFAULT_CONTEXT

        request_id = state.request_id
        start_time = datetime.now()
        perf_start = perf_counter()
        _inputs = {}
        _outputs = {}
        error_msg = None

        try:
            _inputs = self.get_inputs(state, context_id=context_id, parent_context=parent_context)

            if self._is_building:
                raise ValueError(
                    f"Graph {self.name} not built. Must call graph.build() before execution!!"
                )

            _outputs, _ = await self._scheduler.run(state, context_id, parent_context, request_id)

            if self._loop_config:
                _outputs = await self._run_loop(
                    state, context_id, parent_context, request_id, _outputs
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
            end_time = datetime.now()
            duration_ms = (perf_counter() - perf_start) * 1000
            self._log(request_id, context_id, _inputs, _outputs, duration_ms)
            self._store_metrics(
                state,
                context_id,
                error=error_msg,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
            )

            return _outputs


from hush.core.ops.graph._decorators import graph  # noqa: E402, F401
