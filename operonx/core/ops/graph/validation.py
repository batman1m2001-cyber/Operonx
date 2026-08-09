"""Validation types and functions for graph structure validation."""

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional

from operonx.core.loggings import LOGGER
from operonx.core.states.ref import Ref
from operonx.core.utils.algo import find_cycles, reachable

if TYPE_CHECKING:
    from operonx.core.ops.base import BaseOp


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
            f"Graph '{result.graph_name}' validation failed with "
            f"{len(result.errors)} error(s). See logs above for details."
        )


# =========================================================================
# Validation functions (operate on graph data, called by GraphOp.validate)
# =========================================================================


def validate_graph(
    name: str,
    ops: Dict[str, "BaseOp"],
    edges: dict,
    prevs: dict,
    nexts: dict,
    entries: list,
    exits: list,
    ancestor_names: set = None,
) -> ValidationResult:
    """Run all validations on a graph and return result.

    Args:
        ancestor_names: names of ancestor GraphOps in the parent chain.
            Refs pointing to any of these are considered valid (Phase 3: SCC
            ops moved into a synthetic loop keep their original outer PARENT
            refs, so the moved op sees the outer graph name).
    """
    result = ValidationResult(graph_name=name)
    result.issues.extend(_validate_branch_targets(ops))
    result.issues.extend(_validate_cycles(ops, edges))
    result.issues.extend(_validate_reachability(ops, nexts, prevs, entries, exits))
    result.issues.extend(_validate_refs(ops, name, ancestor_names or set()))

    for issue in result.warnings:
        LOGGER.warning("Graph '%s': %s", name, issue.message)

    return result


def _validate_branch_targets(ops: Dict[str, "BaseOp"]) -> List[ValidationIssue]:
    """Check all branch op targets exist in graph."""
    issues = []
    available = sorted(ops.keys())

    for op_name, child in ops.items():
        if child.type != "branch":
            continue
        for target in getattr(child, "candidates", []):
            if target == "__END__":
                continue
            if target not in ops:
                issues.append(
                    ValidationIssue(
                        level=ValidationLevel.ERROR,
                        category="Invalid branch target",
                        message=f"Branch op '{op_name}' references target '{target}' which doesn't exist",
                        op_name=op_name,
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


def _validate_cycles(ops: Dict[str, "BaseOp"], edges: dict) -> List[ValidationIssue]:
    """Detect cycles in the graph (excluding lookback edges)."""
    issues = []
    adj: Dict[str, List[str]] = defaultdict(list)
    for edge in edges.values():
        if edge.type != "lookback":
            adj[edge.from_node].append(edge.to_node)

    cycles = find_cycles(list(ops.keys()), adj)

    for cycle in cycles:
        issues.append(
            ValidationIssue(
                level=ValidationLevel.WARNING,
                category="Cycle detected",
                message=f"Circular dependency found: {' -> '.join(cycle)}",
                op_name=cycle[0],
                suggestions=[
                    "Use lookback edge type for intentional cycles",
                    "Check if this cycle is intentional (e.g., retry logic)",
                    "Break the cycle by restructuring the flow",
                ],
            )
        )
    return issues


def _validate_reachability(
    ops: Dict[str, "BaseOp"],
    nexts: dict,
    prevs: dict,
    entries: list,
    exits: list,
) -> List[ValidationIssue]:
    """Check for orphan ops, unreachable ops, and dead-ends."""
    from operonx.core.ops.base import BaseOp

    issues = []

    for op_name, child in ops.items():
        if (
            not prevs[op_name]
            and not nexts[op_name]
            and not child.start
            and not child.end
            and op_name != BaseOp.INNER_PROCESS
        ):
            issues.append(
                ValidationIssue(
                    level=ValidationLevel.WARNING,
                    category="Orphan op",
                    message=f"Op '{op_name}' has no edges and will never be executed",
                    op_name=op_name,
                    suggestions=[
                        f"Connect START >> {op_name} or connect from another op",
                        "Remove this op if it's not needed",
                    ],
                )
            )

    if not entries or not exits:
        return issues

    # Forward reachability — include branch candidates as extra neighbors
    def branch_neighbors(node):
        if ops[node].type == "branch":
            return [t for t in getattr(ops[node], "candidates", []) if t in ops]
        return []

    reachable_from_start = reachable(entries, nexts, extra_neighbors=branch_neighbors)

    # Backward reachability from exits
    reachable_to_end = reachable(exits, prevs)

    for op_name in ops:
        if op_name not in reachable_from_start:
            issues.append(
                ValidationIssue(
                    level=ValidationLevel.WARNING,
                    category="Unreachable op",
                    message=f"Op '{op_name}' is not reachable from any entry point",
                    op_name=op_name,
                    suggestions=[
                        f"Connect START >> {op_name} or connect from another reachable op",
                        "Remove this op if it's not needed",
                    ],
                )
            )
        if (
            op_name not in reachable_to_end
            and op_name in reachable_from_start
            and not ops[op_name].end
        ):
            issues.append(
                ValidationIssue(
                    level=ValidationLevel.WARNING,
                    category="Dead-end op",
                    message=f"Op '{op_name}' cannot reach any exit point",
                    op_name=op_name,
                    suggestions=[
                        f"Connect {op_name} >> END or connect to another op leading to END",
                        "Mark this op as an exit: op.end = True",
                    ],
                )
            )

    return issues


def _validate_refs(
    ops: Dict[str, "BaseOp"],
    graph_name: str,
    ancestor_names: set = None,
) -> List[ValidationIssue]:
    """Validate all Ref references point to existing ops.

    Args:
        ancestor_names: set of ancestor GraphOp names. Refs pointing to any of
            these are considered valid (Phase 3 rewrite moves SCC ops into a
            hidden loop; their PARENT refs still target the outer graph).
    """
    issues = []
    ancestor_names = ancestor_names or set()

    for op_name, child in ops.items():
        for var, param in child.inputs.items():
            if not isinstance(param.value, Ref):
                continue
            ref_source = param.value.raw_source
            # Skip PARENT refs
            if hasattr(ref_source, "name") and ref_source.name == "__PARENT__":
                continue
            if hasattr(ref_source, "name"):
                ref_op_name = ref_source.name
                if (
                    ref_op_name not in ops
                    and ref_op_name != graph_name
                    and ref_op_name not in ancestor_names
                ):
                    issues.append(
                        ValidationIssue(
                            level=ValidationLevel.ERROR,
                            category="Invalid Ref",
                            message=f"Op '{op_name}' input '{var}' references non-existent op '{ref_op_name}'",
                            op_name=op_name,
                            target_name=ref_op_name,
                            available_nodes=sorted(ops.keys()),
                            suggestions=[
                                f"Check if op '{ref_op_name}' is defined in the graph",
                                "Ensure the referenced op is created before this op",
                            ],
                        )
                    )
    return issues
