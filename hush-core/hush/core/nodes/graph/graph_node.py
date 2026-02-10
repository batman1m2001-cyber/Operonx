"""Graph node để quản lý subgraph các node trong workflow."""

import asyncio
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from time import perf_counter
from typing import Any, Dict, List, Literal, Optional, Set

from hush.core.configs.edge_config import EdgeConfig, EdgeType
from hush.core.configs.node_config import NodeType
from hush.core.loggings import LOGGER
from hush.core.nodes.base import END, PARENT, START, BaseNode
from hush.core.states import MemoryState, Ref
from hush.core.utils.bimap import BiMap
from hush.core.utils.common import Param
from hush.core.utils.context import _current_graph

NodeFlowType = Literal["MERGE", "FORK", "BLOOM", "BRANCH", "NORMAL", "OTHER"]


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
    node_name: Optional[str] = None
    target_name: Optional[str] = None
    available_nodes: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [f"[{self.level.value.upper()}] {self.category}: {self.message}"]

        if self.node_name:
            location = f"  Location: {self.node_name}"
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
                    issue.node_name,
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


class GraphNode(BaseNode):
    """Node chứa và quản lý một subgraph các node.

    Cho phép tổ chức các node thành subgraph tái sử dụng với thực thi
    song song các nhánh và điều khiển luồng phù hợp.

    Hỗ trợ:
    - Context manager (with GraphNode() as graph)
    - Entry/exit node tự động
    - Thực thi song song các node độc lập
    - Soft/hard edge cho branch merging
    """

    __slots__ = [
        "_token",
        "_nodes",
        "entries",
        "exits",
        "prevs",
        "nexts",
        "ready_count",
        "has_soft_preds",  # Set of nodes that have soft predecessors
        "flowtype_map",
        "_edges",
        "_edges_lookup",
        "_is_building",
    ]

    type: NodeType = "graph"

    def __init__(self, **kwargs):
        """Khởi tạo GraphNode."""
        super().__init__(**kwargs)
        self._token = None
        self._is_building = True
        self._nodes: Dict[str, BaseNode] = {}
        self._edges = []
        self._edges_lookup = {}
        self.entries = []
        self.exits = []
        self.prevs = defaultdict(list)
        self.nexts = defaultdict(list)
        self.flowtype_map = BiMap[str, NodeFlowType]()
        self.has_soft_preds = set()  # Các node có soft predecessor

    def __enter__(self):
        """Vào chế độ context manager."""
        self._token = _current_graph.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Thoát chế độ context manager."""
        _current_graph.reset(self._token)

    def _setup_endpoints(self):
        """Khởi tạo entry/exit node."""
        LOGGER.debug("Graph [highlight]%s[/highlight]: đang khởi tạo endpoints...", self.name)

        if not self.entries:
            self.entries = [node for node in self._nodes if not self.prevs[node]]

        if not self.exits:
            self.exits = [node for node in self._nodes if not self.nexts[node]]

        if not self.entries:
            LOGGER.error(
                "Graph [highlight]%s[/highlight]: không tìm thấy entry node. Kiểm tra kết nối START >> node.",
                self.name,
            )
            raise ValueError("Graph phải có ít nhất một entry node.")
        if not self.exits:
            LOGGER.error(
                "Graph [highlight]%s[/highlight]: không tìm thấy exit node. Kiểm tra kết nối node >> END.",
                self.name,
            )
            raise ValueError("Graph phải có ít nhất một exit node.")

    def _setup_schema(self):
        """Khởi tạo inputs/outputs từ các node con.

        Scan các node con để tìm các ref trỏ đến PARENT (self) -
        đó chính là inputs/outputs của graph.
        """
        LOGGER.debug("Graph [highlight]%s[/highlight]: đang tạo schema...", self.name)
        graph_inputs = {}
        graph_outputs = {}

        for _, node in self._nodes.items():
            # Kiểm tra inputs: nếu ref trỏ đến self (father), đó là graph input
            for var, param in node.inputs.items():
                if isinstance(param.value, Ref) and param.value.raw_node is self:
                    # PARENT["x"] resolve thành father - đây là graph input
                    # Copy Param từ node con, giữ nguyên type/required/default/description
                    graph_inputs[param.value.var] = Param(
                        type=param.type,
                        required=param.required,
                        default=param.default,
                        description=param.description,
                    )

            # Kiểm tra outputs: nếu ref trỏ đến self (father), đó là graph output
            for var, param in node.outputs.items():
                if isinstance(param.value, Ref) and param.value.raw_node is self:
                    # PARENT["x"] resolve thành father - đây là graph output
                    # Copy Param từ node con
                    graph_outputs[param.value.var] = Param(
                        type=param.type,
                        required=param.required,
                        default=param.default,
                        description=param.description,
                    )

        # Merge với user-provided inputs/outputs (nếu có)
        self.inputs = self._merge_params(graph_inputs, self.inputs)
        self.outputs = self._merge_params(graph_outputs, self.outputs)

    def _build_flow_type(self):
        """Xác định flow type của mỗi node dựa trên pattern kết nối."""
        LOGGER.debug(
            "Graph [highlight]%s[/highlight]: đang xác định flow type của các node...", self.name
        )
        self.flowtype_map = BiMap[str, NodeFlowType]()

        # Phát hiện orphan node (không có kết nối nào)
        orphan_nodes = []

        for name, node in self._nodes.items():
            prev_count = len(self.prevs[name])
            next_count = len(self.nexts[name])

            # Kiểm tra orphan node (không phải start/end, không phải inner graph, và không có kết nối)
            if (
                prev_count == 0
                and next_count == 0
                and not node.start
                and not node.end
                and name != BaseNode.INNER_PROCESS
            ):
                orphan_nodes.append(name)

            flow_type: NodeFlowType = "OTHER"

            if node.type == "branch":
                flow_type = "BRANCH"
                for target in node.candidates:
                    if target in self._nodes and len(self.nexts[target]) == 1:
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

        # Cảnh báo về orphan node
        if orphan_nodes:
            LOGGER.warning(
                "Graph [highlight]%s[/highlight]: phát hiện orphan node [muted](không có edge)[/muted]: %s. Các node này sẽ không bao giờ được thực thi.",
                self.full_name,
                orphan_nodes,
            )

    def build(self):
        """Build graph bằng cách build các node con trước, sau đó graph này."""
        for node in self._nodes.values():
            if hasattr(node, "build"):
                node.build()

        self._setup_schema()
        self._build_flow_type()
        self._setup_endpoints()

        # Tính ready_count:
        # - Hard edge (>>) đếm từng cái một
        # - Soft edge (>) của cùng node đích đếm chung là 1 (chờ BẤT KỲ một soft pred hoàn thành)
        # Ví dụ: A >> D, B > D, C > D => ready_count[D] = 2 (1 hard + 1 soft group)
        self.ready_count = {}
        self.has_soft_preds = set()
        for name in self._nodes:
            hard_pred_count = 0
            has_soft = False
            for pred in self.prevs[name]:
                edge = self._edges_lookup.get((pred, name))
                if edge and edge.soft:
                    has_soft = True
                elif edge and not edge.soft:
                    hard_pred_count += 1
                elif edge is None:
                    # Edge không tìm thấy trong lookup (không nên xảy ra, nhưng vẫn đếm)
                    hard_pred_count += 1
            # Soft edges đếm chung là 1 nếu có
            if has_soft:
                self.has_soft_preds.add(name)
                hard_pred_count += 1
            self.ready_count[name] = hard_pred_count

        # Run full validation (errors will raise, warnings will be logged)
        result = self.validate()
        result.raise_if_errors()

        self._is_building = False
        self._post_build()

    def _post_build(self):
        """Hook for subclasses to run after build. Override in subclasses."""
        pass

    # =========================================================================
    # Validation Methods
    # =========================================================================

    def validate(self) -> ValidationResult:
        """Run all validations and return result.

        This method runs comprehensive validation checks on the graph:
        - Branch targets: All if_() targets must match actual node names
        - Cycles: Detect circular dependencies (non-lookback)
        - Reachability: Warn about unreachable or dead-end nodes
        - Refs: Validate all Ref references point to existing nodes

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
        """Check all branch node targets exist in graph."""
        issues = []
        available = sorted(self._nodes.keys())

        for name, node in self._nodes.items():
            if node.type != "branch":
                continue

            # Get all possible targets from branch node
            candidates = getattr(node, "candidates", [])

            for target in candidates:
                if target == END.name:
                    continue  # END is always valid

                if target not in self._nodes:
                    issues.append(
                        ValidationIssue(
                            level=ValidationLevel.ERROR,
                            category="Invalid branch target",
                            message=f"Branch node '{name}' references target '{target}' which doesn't exist",
                            node_name=name,
                            target_name=target,
                            available_nodes=available,
                            suggestions=[
                                f"Check if '{target}' matches the 'name' parameter of the target node",
                                f'Use the node variable directly: if_(condition, my_node) instead of if_(condition, "{target}")',
                                f"Available nodes: {available}",
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
        color = {name: WHITE for name in self._nodes}
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

        for node in self._nodes:
            if color[node] == WHITE:
                dfs(node, [])

        for cycle in cycle_nodes:
            cycle_str = " -> ".join(cycle)
            issues.append(
                ValidationIssue(
                    level=ValidationLevel.WARNING,
                    category="Cycle detected",
                    message=f"Circular dependency found: {cycle_str}",
                    node_name=cycle[0],
                    suggestions=[
                        "Use lookback edge type for intentional cycles",
                        "Check if this cycle is intentional (e.g., retry logic)",
                        "Break the cycle by restructuring the flow",
                    ],
                )
            )

        return issues

    def _validate_reachability(self) -> List[ValidationIssue]:
        """Check for unreachable nodes and dead-ends."""
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
            if self._nodes[node].type == "branch":
                for target in getattr(self._nodes[node], "candidates", []):
                    if target in self._nodes:
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

        # Find unreachable nodes
        for name in self._nodes:
            if name not in reachable_from_start:
                issues.append(
                    ValidationIssue(
                        level=ValidationLevel.WARNING,
                        category="Unreachable node",
                        message=f"Node '{name}' is not reachable from any entry point",
                        node_name=name,
                        suggestions=[
                            f"Connect START >> {name} or connect from another reachable node",
                            "Remove this node if it's not needed",
                        ],
                    )
                )

            if name not in reachable_to_end and name in reachable_from_start:
                # Only warn about dead-ends that are reachable
                if not self._nodes[name].end:
                    issues.append(
                        ValidationIssue(
                            level=ValidationLevel.WARNING,
                            category="Dead-end node",
                            message=f"Node '{name}' cannot reach any exit point",
                            node_name=name,
                            suggestions=[
                                f"Connect {name} >> END or connect to another node leading to END",
                                "Mark this node as an exit: node.end = True",
                            ],
                        )
                    )

        return issues

    def _validate_refs(self) -> List[ValidationIssue]:
        """Validate all Ref references point to existing nodes."""
        issues = []

        for name, node in self._nodes.items():
            # Check input refs
            for var, param in node.inputs.items():
                if not isinstance(param.value, Ref):
                    continue

                ref = param.value
                ref_node = ref.raw_node

                # Skip PARENT refs (they refer to the graph itself)
                if ref_node is self or (
                    hasattr(ref_node, "name") and ref_node.name == "__PARENT__"
                ):
                    continue

                # Check if referenced node exists
                if hasattr(ref_node, "name"):
                    ref_node_name = ref_node.name
                    if ref_node_name not in self._nodes and ref_node_name != self.name:
                        issues.append(
                            ValidationIssue(
                                level=ValidationLevel.ERROR,
                                category="Invalid Ref",
                                message=f"Node '{name}' input '{var}' references non-existent node '{ref_node_name}'",
                                node_name=name,
                                target_name=ref_node_name,
                                available_nodes=sorted(self._nodes.keys()),
                                suggestions=[
                                    f"Check if node '{ref_node_name}' is defined in the graph",
                                    "Ensure the referenced node is created before this node",
                                ],
                            )
                        )

        return issues

    @staticmethod
    def get_current_graph() -> Optional["GraphNode"]:
        """Lấy graph hiện tại từ context."""
        try:
            return _current_graph.get()
        except LookupError:
            return None

    def add_node(self, node: BaseNode) -> BaseNode:
        """Thêm một node vào graph."""
        if not self._is_building:
            raise RuntimeError("Không thể thêm node sau khi graph đã được build")

        if getattr(node, "_is_hush_builder", False):
            name = getattr(node, "_name", None) or type(node).__name__
            LOGGER.error(
                "%s '%s' chưa được build. Hãy gọi .build() hoặc .else_() trước khi thêm vào graph.",
                type(node).__name__,
                name,
            )
            raise TypeError(
                f"{type(node).__name__} '{name}' chưa được build. "
                f"Hãy gọi .build() hoặc .else_() để tạo node."
            )

        if node in [START, END]:
            return node

        # Cảnh báo nếu node cùng tên đã tồn tại (sẽ bị ghi đè)
        if node.name in self._nodes:
            LOGGER.warning(
                "Graph [highlight]%s[/highlight]: node [highlight]%s[/highlight] đã tồn tại và sẽ bị ghi đè",
                self.name,
                node.name,
            )

        self._nodes[node.name] = node

        if hasattr(node, "start") and node.start:
            if node.name not in self.entries:
                self.entries.append(node.name)

        if hasattr(node, "end") and node.end:
            if node.name not in self.exits:
                self.exits.append(node.name)

        return node

    def add_edge(self, source: str, target: str, type: EdgeType = "normal", soft: bool = False):
        """Thêm một edge giữa hai node.

        Args:
            source: Tên node nguồn
            target: Tên node đích
            type: Loại edge (normal, lookback, condition)
            soft: Nếu True, edge không tính vào ready_count.
                  Dùng cho branch output khi chỉ một nhánh được thực thi.
                  Tạo bằng toán tử >: case_a > merge_node
        """
        if not self._is_building:
            raise RuntimeError("Không thể thêm edge sau khi graph đã được build!")

        if source == START.name:
            if target not in self._nodes:
                raise ValueError(f"Node đích '{target}' không tìm thấy")

            target_node = self._nodes[target]
            target_node.start = True

            if target not in self.entries:
                self.entries.append(target)

            return

        if target == END.name:
            if source not in self._nodes:
                raise ValueError(f"Node nguồn '{source}' không tìm thấy")

            source_node = self._nodes[source]
            source_node.end = True

            if source not in self.exits:
                self.exits.append(source)

            return

        if target == PARENT.name:
            return

        if source not in self._nodes:
            raise ValueError(f"Node nguồn '{source}' không tìm thấy")
        if target not in self._nodes:
            raise ValueError(f"Node đích '{target}' không tìm thấy")

        new_edge = EdgeConfig(from_node=source, to_node=target, type=type, soft=soft)
        if (source, target) not in self._edges_lookup:
            self._edges.append(new_edge)
            self._edges_lookup[source, target] = new_edge
            self.nexts[source].append(target)
            self.prevs[target].append(source)

    def show(self, indent=0):
        """Hiển thị cấu trúc graph (debug)."""
        prefix = "  " * indent
        LOGGER.debug("%sGraph: %s", prefix, self.name)
        LOGGER.debug("%sNodes: %s", prefix, list(self._nodes.keys()))
        LOGGER.debug("%sEdges:", prefix)
        for edge in self._edges:
            soft_marker = " (soft)" if edge.soft else ""
            LOGGER.debug(
                "%s  %s -> %s: %s%s", prefix, edge.from_node, edge.to_node, edge.type, soft_marker
            )
        LOGGER.debug("%sReady count: %s", prefix, dict(self.ready_count))

        for node in self._nodes.values():
            if isinstance(node, GraphNode):
                node.show(indent + 1)

    async def run(
        self,
        state: "MemoryState",
        context_id: Optional[str] = None,
        parent_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Thực thi graph bằng cách chạy tất cả node theo thứ tự dependency.

        Args:
            state: Workflow state
            context_id: Context của graph này
            parent_context: Context của PARENT để truyền cho child nodes
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
            # Track nodes đã nhận soft edge completion (chỉ đếm 1 lần)
            soft_satisfied: set = set()

            for entry in self.entries:
                task = asyncio.create_task(
                    name=entry, coro=self._nodes[entry].run(state, context_id, parent_context)
                )
                active_tasks[entry] = task

            while active_tasks:
                done_tasks, _ = await asyncio.wait(
                    active_tasks.values(), return_when=asyncio.FIRST_COMPLETED
                )

                # Cache dict lookups for performance
                nodes = self._nodes
                nexts = self.nexts
                edges_lookup = self._edges_lookup

                for task in done_tasks:
                    node_name = task.get_name()
                    active_tasks.pop(node_name)

                    node = nodes[node_name]

                    if node.type == "branch":
                        branch_target = node.get_target(state, context_id)
                        if branch_target != END.name:
                            next_nodes = [branch_target]
                        else:
                            next_nodes = []
                    else:
                        next_nodes = nexts[node_name]

                    for next_node in next_nodes:
                        # Validate node exists in graph
                        if next_node not in ready_count:
                            available_nodes = sorted(ready_count.keys())
                            raise KeyError(
                                f"\n"
                                f"Node '{next_node}' not found in graph '{self.name}'.\n"
                                f"\n"
                                f"  Source: Branch node '{node_name}' routed to '{next_node}'\n"
                                f"  Available nodes: {available_nodes}\n"
                                f"\n"
                                f'  This usually means the target string in if_(..., "{next_node}") '
                                f"doesn't match any node's actual name.\n"
                                f"  Check that your branch targets match the 'name' parameter of the target nodes."
                            )

                        # Kiểm tra edge type
                        edge = edges_lookup.get((node_name, next_node))
                        is_soft = edge and edge.soft

                        if is_soft:
                            # Soft edge: chỉ đếm 1 lần cho tất cả soft predecessors
                            if next_node in soft_satisfied:
                                continue  # Đã có soft pred khác hoàn thành
                            soft_satisfied.add(next_node)

                        ready_count[next_node] -= 1

                        if ready_count[next_node] == 0:
                            task = asyncio.create_task(
                                name=next_node,
                                coro=nodes[next_node].run(state, context_id, parent_context),
                            )
                            active_tasks[next_node] = task

            _outputs = self.get_outputs(state, context_id=context_id, parent_context=parent_context)
            self.store_result(state, _outputs, context_id)

        except Exception:
            error_msg = traceback.format_exc()
            LOGGER.error(
                "[title]\\[%s][/title] Error in node [highlight]%s[/highlight]:\n%s",
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
                node_name=self.full_name,
                context_id=context_id,
                name=self.name,
                input_vars=list(self.inputs.keys()) if self.inputs else [],
                output_vars=list(self.outputs.keys()) if self.outputs else [],
                parent_name=parent_name,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
                contain_generation=False,
                metadata=self._cached_metadata(),
            )
            return _outputs


# Alias đơn giản cho cú pháp gọn hơn
graph = GraphNode
