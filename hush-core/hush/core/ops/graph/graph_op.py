"""GraphOp — container op that manages a graph of child ops."""

import asyncio
import inspect
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from functools import wraps
from time import perf_counter
from typing import Any, Dict, List, Literal, Optional, Set

from hush.core.configs.edge_config import EdgeConfig, EdgeType
from hush.core.configs.op_config import OpType
from hush.core.loggings import LOGGER
from hush.core.ops.base import (
    _BASE_INIT_KEYS,
    END,
    PARENT,
    START,
    BaseOp,
    split_shorthand_kwargs,
)
from hush.core.states import MemoryState, Ref
from hush.core.utils.auto_name import register_skip
from hush.core.utils.bimap import BiMap
from hush.core.utils.common import Param
from hush.core.utils.context import _current_graph

OpFlowType = Literal["MERGE", "FORK", "BLOOM", "BRANCH", "NORMAL", "OTHER"]


# =============================================================================
# Validation Types
# =============================================================================


class ValidationLevel(Enum):
    """Severity level for validation issues."""

    ERROR = "error"  # Must fix, will raise exception
    WARNING = "warning"  # Should fix, logged as warning


@dataclass
class ValidationIssue:
    """A single validation issue found in the graph."""

    level: ValidationLevel
    category: str
    message: str
    op_name: Optional[str] = None
    target_name: Optional[str] = None
    available_nodes: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [f"[{self.level.value.upper()}] {self.category}: {self.message}"]

        if self.op_name:
            location = f"  Location: {self.op_name}"
            if self.target_name:
                location += f" -> '{self.target_name}'"
            lines.append(location)

        if self.available_nodes:
            lines.append(f"  Available nodes: {self.available_nodes}")

        if self.suggestions:
            lines.append("  How to fix:")
            for suggestion in self.suggestions:
                lines.append(f"    - {suggestion}")

        return "\n".join(lines)


@dataclass
class ValidationResult:
    """Result of graph validation."""

    graph_name: str
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(issue.level == ValidationLevel.ERROR for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(issue.level == ValidationLevel.WARNING for issue in self.issues)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == ValidationLevel.ERROR]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.level == ValidationLevel.WARNING]

    def __str__(self) -> str:
        if not self.issues:
            return f"Graph '{self.graph_name}': All validations passed"

        lines = [f"Graph '{self.graph_name}' validation found {len(self.issues)} issue(s):"]
        lines.append("")
        for i, issue in enumerate(self.issues, 1):
            lines.append(f"{i}. {issue}")
            lines.append("")
        return "\n".join(lines)

    def raise_if_errors(self):
        """Raise exception if there are any errors."""
        if self.has_errors:
            LOGGER.error(
                "Graph [highlight]%s[/highlight] validation found %d error(s):",
                self.graph_name,
                len(self.errors),
            )
            for issue in self.errors:
                LOGGER.error(
                    "  [%s] %s: %s | Location: %s -> '%s' | Available nodes: %s",
                    issue.level.value.upper(),
                    issue.category,
                    issue.message,
                    issue.op_name,
                    issue.target_name,
                    issue.available_nodes,
                )
            raise GraphValidationError(self)


class GraphValidationError(Exception):
    """Exception raised when graph validation fails."""

    def __init__(self, result: ValidationResult):
        self.result = result
        super().__init__(
            f"Graph '{result.graph_name}' validation failed with {len(result.errors)} error(s). See logs above for details."
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
        "ready_count",
        "has_soft_preds",  # Set of ops that have soft predecessors
        "flowtype_map",
        "_edges",
        "_edges_lookup",
        "_is_building",
        "_compiled_adj",
    ]

    type: OpType = "graph"

    def __init__(self, **kwargs):
        """Khởi tạo GraphOp."""
        super().__init__(**kwargs)
        self._token = None
        self._is_building = True
        self._ops: Dict[str, BaseOp] = {}
        self._edges = []
        self._edges_lookup = {}
        self.entries = []
        self.exits = []
        self.prevs = defaultdict(list)
        self.nexts = defaultdict(list)
        self.flowtype_map = BiMap[str, OpFlowType]()
        self.has_soft_preds = set()  # Ops with soft predecessors
        self._compiled_adj = {}

    def __enter__(self):
        """Enter context manager mode."""
        self._token = _current_graph.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager mode."""
        _current_graph.reset(self._token)
        if exc_type is None:
            self._setup_schema()

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
            # Check inputs: if ref points to self (father), it's a graph input
            for var, param in child.inputs.items():
                if isinstance(param.value, Ref) and param.value.raw_source is self:
                    # PARENT["x"] resolves to father — this is a graph input
                    graph_inputs[param.value.var] = Param(
                        type=param.type,
                        required=param.required,
                        default=param.default,
                        description=param.description,
                    )

            # Check outputs: if ref points to self (father), it's a graph output
            for var, param in child.outputs.items():
                if isinstance(param.value, Ref) and param.value.raw_source is self:
                    # PARENT["x"] resolves to father — this is a graph output
                    graph_outputs[param.value.var] = Param(
                        type=param.type,
                        required=param.required,
                        default=param.default,
                        description=param.description,
                    )

        # Merge with user-provided inputs/outputs (if any)
        self.inputs = self._merge_params(graph_inputs, self.inputs)
        self.outputs = self._merge_params(graph_outputs, self.outputs)

    def _build_flow_type(self):
        """Determine the flow type of each op based on its connectivity pattern."""
        LOGGER.debug("Graph [highlight]%s[/highlight]: determining flow types...", self.name)
        self.flowtype_map = BiMap[str, OpFlowType]()

        # Detect orphan ops (no connections at all)
        orphan_ops = []

        for name, child in self._ops.items():
            prev_count = len(self.prevs[name])
            next_count = len(self.nexts[name])

            # Check orphan op (not start/end, not inner graph, and no connections)
            if (
                prev_count == 0
                and next_count == 0
                and not child.start
                and not child.end
                and name != BaseOp.INNER_PROCESS
            ):
                orphan_ops.append(name)

            flow_type: OpFlowType = "OTHER"

            if child.type == "branch":
                flow_type = "BRANCH"
                for target in child.candidates:
                    if target in self._ops and len(self.nexts[target]) == 1:
                        self.flowtype_map[target] = "NORMAL"

            elif prev_count > 1 and next_count > 1:
                flow_type = "BLOOM"
            elif prev_count > 1 and next_count == 1:
                flow_type = "MERGE"
            elif prev_count == 1 and next_count > 1:
                flow_type = "FORK"
            elif prev_count == 1 and next_count == 1:
                flow_type = "NORMAL"

            self.flowtype_map[name] = flow_type

        # Warn about orphan ops
        if orphan_ops:
            LOGGER.warning(
                "Graph [highlight]%s[/highlight]: detected orphan ops [muted](no edges)[/muted]: %s. These ops will never be executed.",
                self.full_name,
                orphan_ops,
            )

    def build(self):
        """Build graph by building child ops first, then this graph."""
        for child in self._ops.values():
            if hasattr(child, "build"):
                child.build()

        self._setup_schema()
        self._build_flow_type()
        self._setup_endpoints()

        # Compute ready_count:
        # - Hard edge (>>) counts individually
        # - Soft edges (>) to the same target count as 1 (wait for ANY soft pred to complete)
        # Example: A >> D, B > D, C > D => ready_count[D] = 2 (1 hard + 1 soft group)
        self.ready_count = {}
        self.has_soft_preds = set()
        for name in self._ops:
            hard_pred_count = 0
            has_soft = False
            for pred in self.prevs[name]:
                edge = self._edges_lookup.get((pred, name))
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
            self.ready_count[name] = hard_pred_count

        # Run full validation (errors will raise, warnings will be logged)
        result = self.validate()
        result.raise_if_errors()

        # Compile adjacency list for faster traversal at runtime
        self._compiled_adj = {}
        for name in self._ops:
            adj = []
            for successor in self.nexts[name]:
                edge = self._edges_lookup.get((name, successor))
                is_soft = bool(edge and edge.soft)
                adj.append((successor, is_soft))
            self._compiled_adj[name] = adj

        self._is_building = False
        self._post_build()
        self._cache_full_names()

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
    # Validation Methods
    # =========================================================================

    def validate(self) -> ValidationResult:
        """Run all validations and return result.

        This method runs comprehensive validation checks on the graph:
        - Branch targets: All if_() targets must match actual op names
        - Cycles: Detect circular dependencies (non-lookback)
        - Reachability: Warn about unreachable or dead-end ops
        - Refs: Validate all Ref references point to existing ops

        Returns:
            ValidationResult with all issues found

        Example:
            result = graph.validate()
            if result.has_errors:
                print(result)
            result.raise_if_errors()  # Raises GraphValidationError if errors
        """
        result = ValidationResult(graph_name=self.name)

        # Run all validations
        result.issues.extend(self._validate_branch_targets_detailed())
        result.issues.extend(self._validate_cycles())
        result.issues.extend(self._validate_reachability())
        result.issues.extend(self._validate_refs())

        # Log warnings
        for issue in result.warnings:
            LOGGER.warning("Graph '%s': %s", self.name, issue.message)

        return result

    def _validate_branch_targets(self):
        """Validate all branch targets exist (called during build, raises on error)."""
        issues = self._validate_branch_targets_detailed()
        errors = [i for i in issues if i.level == ValidationLevel.ERROR]

        if errors:
            result = ValidationResult(graph_name=self.name, issues=errors)
            raise GraphValidationError(result)

    def _validate_branch_targets_detailed(self) -> List[ValidationIssue]:
        """Check all branch op targets exist in graph."""
        issues = []
        available = sorted(self._ops.keys())

        for name, child in self._ops.items():
            if child.type != "branch":
                continue

            # Get all possible targets from branch op
            candidates = getattr(child, "candidates", [])

            for target in candidates:
                if target == END.name:
                    continue  # END is always valid

                if target not in self._ops:
                    issues.append(
                        ValidationIssue(
                            level=ValidationLevel.ERROR,
                            category="Invalid branch target",
                            message=f"Branch op '{name}' references target '{target}' which doesn't exist",
                            op_name=name,
                            target_name=target,
                            available_nodes=available,
                            suggestions=[
                                f"Check if '{target}' matches the 'name' parameter of the target op",
                                f'Use the op variable directly: if_(condition, my_op) instead of if_(condition, "{target}")',
                                f"Available ops: {available}",
                            ],
                        )
                    )

        return issues

    def _validate_cycles(self) -> List[ValidationIssue]:
        """Detect cycles in the graph (excluding lookback edges)."""
        issues = []

        # Build adjacency list excluding lookback edges
        adj: Dict[str, List[str]] = defaultdict(list)
        for edge in self._edges:
            if edge.type != "lookback":
                adj[edge.from_node].append(edge.to_node)

        # DFS-based cycle detection
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {name: WHITE for name in self._ops}
        cycle_nodes = []

        def dfs(node: str, path: List[str]) -> bool:
            color[node] = GRAY
            path.append(node)

            for neighbor in adj[node]:
                if neighbor not in color:
                    continue
                if color[neighbor] == GRAY:
                    # Found cycle
                    cycle_start = path.index(neighbor)
                    cycle_nodes.append(path[cycle_start:] + [neighbor])
                    return True
                if color[neighbor] == WHITE:
                    if dfs(neighbor, path):
                        return True

            path.pop()
            color[node] = BLACK
            return False

        for node in self._ops:
            if color[node] == WHITE:
                dfs(node, [])

        for cycle in cycle_nodes:
            cycle_str = " -> ".join(cycle)
            issues.append(
                ValidationIssue(
                    level=ValidationLevel.WARNING,
                    category="Cycle detected",
                    message=f"Circular dependency found: {cycle_str}",
                    op_name=cycle[0],
                    suggestions=[
                        "Use lookback edge type for intentional cycles",
                        "Check if this cycle is intentional (e.g., retry logic)",
                        "Break the cycle by restructuring the flow",
                    ],
                )
            )

        return issues

    def _validate_reachability(self) -> List[ValidationIssue]:
        """Check for unreachable ops and dead-ends."""
        issues = []

        if not self.entries or not self.exits:
            return issues  # Can't validate without endpoints

        # Forward reachability from entries
        reachable_from_start: Set[str] = set()

        def forward_dfs(node: str):
            if node in reachable_from_start:
                return
            reachable_from_start.add(node)
            for neighbor in self.nexts[node]:
                forward_dfs(neighbor)
            # Also follow branch candidates
            if self._ops[node].type == "branch":
                for target in getattr(self._ops[node], "candidates", []):
                    if target in self._ops:
                        forward_dfs(target)

        for entry in self.entries:
            forward_dfs(entry)

        # Backward reachability from exits
        reachable_to_end: Set[str] = set()

        def backward_dfs(node: str):
            if node in reachable_to_end:
                return
            reachable_to_end.add(node)
            for pred in self.prevs[node]:
                backward_dfs(pred)

        for exit_node in self.exits:
            backward_dfs(exit_node)

        # Find unreachable ops
        for name in self._ops:
            if name not in reachable_from_start:
                issues.append(
                    ValidationIssue(
                        level=ValidationLevel.WARNING,
                        category="Unreachable op",
                        message=f"Op '{name}' is not reachable from any entry point",
                        op_name=name,
                        suggestions=[
                            f"Connect START >> {name} or connect from another reachable op",
                            "Remove this op if it's not needed",
                        ],
                    )
                )

            if name not in reachable_to_end and name in reachable_from_start:
                # Only warn about dead-ends that are reachable
                if not self._ops[name].end:
                    issues.append(
                        ValidationIssue(
                            level=ValidationLevel.WARNING,
                            category="Dead-end op",
                            message=f"Op '{name}' cannot reach any exit point",
                            op_name=name,
                            suggestions=[
                                f"Connect {name} >> END or connect to another op leading to END",
                                "Mark this op as an exit: op.end = True",
                            ],
                        )
                    )

        return issues

    def _validate_refs(self) -> List[ValidationIssue]:
        """Validate all Ref references point to existing ops."""
        issues = []

        for name, child in self._ops.items():
            # Check input refs
            for var, param in child.inputs.items():
                if not isinstance(param.value, Ref):
                    continue

                ref = param.value
                ref_source = ref.raw_source

                # Skip PARENT refs (they refer to the graph itself)
                if ref_source is self or (
                    hasattr(ref_source, "name") and ref_source.name == "__PARENT__"
                ):
                    continue

                # Check if referenced op exists
                if hasattr(ref_source, "name"):
                    ref_op_name = ref_source.name
                    if ref_op_name not in self._ops and ref_op_name != self.name:
                        issues.append(
                            ValidationIssue(
                                level=ValidationLevel.ERROR,
                                category="Invalid Ref",
                                message=f"Op '{name}' input '{var}' references non-existent op '{ref_op_name}'",
                                op_name=name,
                                target_name=ref_op_name,
                                available_nodes=sorted(self._ops.keys()),
                                suggestions=[
                                    f"Check if op '{ref_op_name}' is defined in the graph",
                                    "Ensure the referenced op is created before this op",
                                ],
                            )
                        )

        return issues

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
        if (source, target) not in self._edges_lookup:
            self._edges.append(new_edge)
            self._edges_lookup[source, target] = new_edge
            self.nexts[source].append(target)
            self.prevs[target].append(source)

    def show(self, indent=0):
        """Display graph structure (debug)."""
        prefix = "  " * indent
        LOGGER.debug("%sGraph: %s", prefix, self.name)
        LOGGER.debug("%sOps: %s", prefix, list(self._ops.keys()))
        LOGGER.debug("%sEdges:", prefix)
        for edge in self._edges:
            soft_marker = " (soft)" if edge.soft else ""
            LOGGER.debug(
                "%s  %s -> %s: %s%s", prefix, edge.from_node, edge.to_node, edge.type, soft_marker
            )
        LOGGER.debug("%sReady count: %s", prefix, dict(self.ready_count))

        for child in self._ops.values():
            if isinstance(child, GraphOp):
                child.show(indent + 1)

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

        parent_name = self.father.full_name if self.father else None
        state.record_execution(self.full_name, parent_name, context_id)

        request_id = state.request_id
        start_time = datetime.now()
        perf_start = perf_counter()
        _inputs = {}
        _outputs = {}

        try:
            _inputs = self.get_inputs(state, context_id=context_id, parent_context=parent_context)

            if self._is_building:
                raise ValueError(
                    f"Graph {self.name} not built. Must call graph.build() before execution!!"
                )

            active_tasks: Dict[str, asyncio.Task] = {}

            ready_count: Dict[str, int] = self.ready_count.copy()
            # Track ops that have received soft edge completion (count once only)
            soft_satisfied: set = set()

            for entry in self.entries:
                task = asyncio.create_task(
                    name=entry, coro=self._ops[entry].run(state, context_id, parent_context)
                )
                active_tasks[entry] = task

            while active_tasks:
                done_tasks, _ = await asyncio.wait(
                    active_tasks.values(), return_when=asyncio.FIRST_COMPLETED
                )

                # Cache dict lookups for performance
                nodes = self._ops
                compiled_adj = self._compiled_adj

                for task in done_tasks:
                    op_name = task.get_name()
                    active_tasks.pop(op_name)

                    current_op = nodes[op_name]

                    if current_op.type == "branch":
                        branch_target = current_op.get_target(state, context_id)
                        if branch_target != END.name:
                            # Find the selected target in compiled adjacency
                            targets = []
                            for tgt, soft in compiled_adj[op_name]:
                                if tgt == branch_target:
                                    targets.append((tgt, soft))
                                    break
                            else:
                                # Target not in connections — detailed error
                                available_ops = sorted(ready_count.keys())
                                raise KeyError(
                                    f"\n"
                                    f"Op '{branch_target}' not found in graph '{self.name}'.\n"
                                    f"\n"
                                    f"  Source: Branch op '{op_name}' routed to '{branch_target}'\n"
                                    f"  Available ops: {available_ops}\n"
                                    f"\n"
                                    f'  This usually means the target string in if_(..., "{branch_target}") '
                                    f"doesn't match any op's actual name.\n"
                                    f"  Check that your branch targets match the 'name' parameter of the target ops."
                                )
                        else:
                            targets = []
                    else:
                        targets = compiled_adj[op_name]

                    for next_op, is_soft in targets:
                        if is_soft:
                            # Soft edge: count once for all soft predecessors
                            if next_op in soft_satisfied:
                                continue  # Another soft pred already completed
                            soft_satisfied.add(next_op)

                        ready_count[next_op] -= 1

                        if ready_count[next_op] == 0:
                            task = asyncio.create_task(
                                name=next_op,
                                coro=nodes[next_op].run(state, context_id, parent_context),
                            )
                            active_tasks[next_op] = task

            _outputs = self.get_outputs(state, context_id=context_id, parent_context=parent_context)
            self.store_result(state, _outputs, context_id)

        except Exception:
            error_msg = traceback.format_exc()
            LOGGER.error(
                "[title]\\[%s][/title] Error in op [highlight]%s[/highlight]:\n%s",
                request_id,
                self.name,
                error_msg.rstrip(),
            )
            state[self.full_name, "error", context_id] = error_msg

        finally:
            end_time = datetime.now()
            duration_ms = (perf_counter() - perf_start) * 1000
            self._log(request_id, context_id, _inputs, _outputs, duration_ms)
            state[self.full_name, "start_time", context_id] = start_time
            state[self.full_name, "end_time", context_id] = end_time

            # Record trace metadata for observability
            state.record_trace_metadata(
                op_name=self.full_name,
                context_id=context_id,
                name=self.name,
                input_vars=list(self.inputs.keys()) if self.inputs else [],
                output_vars=list(self.outputs.keys()) if self.outputs else [],
                parent_name=parent_name,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
                contain_generation=False,
                metadata=self.metadata,
            )
            return _outputs


def graph(fn):
    """Decorator to turn a builder function into a reusable GraphOp factory.

    The function's parameters become graph inputs, injected as PARENT refs.
    Auto-naming works through the decorator (via register_skip).

    Example::

        @graph
        def verify_card(conversation):
            check = detect_card(conversation=conversation)
            START >> check >> END

        g1 = verify_card(conversation=other_op["conv"])
        # g1.name == "g1"
    """
    sig = inspect.signature(fn)
    collisions = set(sig.parameters.keys()) & _BASE_INIT_KEYS
    if collisions:
        LOGGER.warning(
            "@graph function '%s' has parameter(s) %s that collide with reserved op keywords %s. "
            "Consider renaming them.",
            fn.__name__,
            sorted(collisions),
            sorted(_BASE_INIT_KEYS),
        )

    param_names = set(sig.parameters.keys())

    @wraps(fn)
    def wrapper(**kwargs):
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        g = GraphOp(inputs=input_mappings or None, **init_kwargs)
        with g:
            parent_refs = {key: PARENT[key] for key in input_mappings if key in param_names}
            fn(**parent_refs)
        return g

    register_skip(wrapper)
    wrapper.__wrapped__ = fn
    return wrapper
