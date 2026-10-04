"""Validation types and functions for graph structure validation."""

import difflib
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Set

from operonx.core.loggings import LOGGER
from operonx.core.states.ref import Ref
from operonx.core.states.schema import OP_META_VARS
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
        details = "\n".join(f"- {issue.message}" for issue in result.errors)
        super().__init__(
            f"Graph '{result.graph_name}' validation failed with "
            f"{len(result.errors)} error(s):\n{details}"
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
    descendant_names: set = None,
    sibling_names: set = None,
    graph: "BaseOp" = None,
    arm_sig: Optional[Callable[[str], Dict[str, set]]] = None,
) -> ValidationResult:
    """Run all validations on a graph and return result.

    Args:
        ancestor_names: names of ancestor GraphOps in the parent chain.
            Refs pointing to any of these are considered valid (Phase 3: SCC
            ops moved into a synthetic loop keep their original outer PARENT
            refs, so the moved op sees the outer graph name).
        descendant_names: names of ops living inside synthetic hidden loops
            that are descendants of this graph. Outer ops may hold refs to
            such moved SCC ops; the op instance is unchanged so state lookups
            resolve at runtime (BUG 3 from Phase 3 review).
        sibling_names: names of ops living in any ancestor graph's ``_ops``.
            A BranchOp moved into a synthetic hidden loop may reference
            siblings that stayed at the outer level (BUG 2 / E3 multi-exit-
            via-branch): the branch's ``__branch_target__`` is re-routed at
            the outer level through the loop's outgoing edges.
        graph: the graph itself, for the checks that read its declared
            cells (concurrent writers). ``arm_sig`` is its
            ``_arm_signatures()``: which ops never both run.
    """
    ancestor_names = ancestor_names or set()
    descendant_names = descendant_names or set()
    sibling_names = sibling_names or set()
    result = ValidationResult(graph_name=name)
    result.issues.extend(_validate_branch_targets(ops, descendant_names, sibling_names))
    result.issues.extend(_validate_cycles(ops, edges))
    result.issues.extend(_validate_reachability(ops, nexts, prevs, entries, exits))
    result.issues.extend(_validate_refs(ops, name, ancestor_names, descendant_names, sibling_names))
    result.issues.extend(_validate_output_keys(ops))
    if graph is not None:
        result.issues.extend(_validate_concurrent_writers(graph, ops, nexts, arm_sig))
        result.issues.extend(_validate_ordered_reads(graph, ops, prevs))

    for issue in result.warnings:
        LOGGER.warning("Graph '%s': %s", name, issue.message)

    return result


def _validate_branch_targets(
    ops: Dict[str, "BaseOp"],
    descendant_names: set = None,
    sibling_names: set = None,
) -> List[ValidationIssue]:
    """Check all branch op targets exist in graph.

    Args:
        descendant_names: names of ops living inside synthetic hidden loops
            that descend from this graph. A branch inside such a loop may
            legitimately reference sibling ops that stayed at the outer level
            (Phase 3 E3 multi-exit case): the branch's __branch_target__ is
            re-routed at the outer level via the loop's outgoing edges.
        sibling_names: names of ops in ancestor graphs' ``_ops`` — same
            purpose as descendant_names but for branches inside a synthetic
            hidden loop that reference OUTER siblings (BUG 2).
    """
    issues = []
    descendant_names = descendant_names or set()
    sibling_names = sibling_names or set()
    available = sorted(ops.keys())

    for op_name, child in ops.items():
        if child.type != "branch":
            continue
        for target in getattr(child, "candidates", []):
            if target == "__END__":
                continue
            if target in ops:
                continue
            if target in descendant_names or target in sibling_names:
                # SCC-inside-loop referencing sibling / cousin (BUG 2 fix).
                continue
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
        unwired = (
            not prevs[op_name]
            and not nexts[op_name]
            and not child.start
            and not child.end
            and op_name != BaseOp.INNER_PROCESS
        )
        if unwired and op_name in entries:
            # A graph with no `START >>` takes every op without a predecessor
            # as an entry, so an op with no edges at all does run — at once,
            # beside every other entry, with nothing waiting for it. Saying
            # it would never run was false, and hid exactly that race.
            issues.append(
                ValidationIssue(
                    level=ValidationLevel.WARNING,
                    category="Unwired op",
                    message=(
                        f"Op '{op_name}' has no edges: it runs as an entry of the graph, "
                        f"at the same time as every other entry, and nothing waits for "
                        f"it. Wire it: START >> {op_name} >> ..."
                    ),
                    op_name=op_name,
                    suggestions=[f"START >> {op_name} >> next_op"],
                )
            )
        elif unwired:
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
    descendant_names: set = None,
    sibling_names: set = None,
) -> List[ValidationIssue]:
    """Validate all Ref references point to existing ops.

    Args:
        ancestor_names: set of ancestor GraphOp names. Refs pointing to any of
            these are considered valid (Phase 3 rewrite moves SCC ops into a
            hidden loop; their PARENT refs still target the outer graph).
        descendant_names: set of op names living inside synthetic hidden
            loops that descend from this graph. Outer ops may hold refs to
            such moved SCC ops after Phase 3 rewrite; the op instance is
            unchanged so state lookups by full_name still resolve.
        sibling_names: set of op names living in any ancestor graph's ``_ops``.
            A moved SCC op inside a synthetic loop may hold a Ref to a
            sibling that stayed at the outer level (BUG 2 / E3 case).
    """
    issues = []
    ancestor_names = ancestor_names or set()
    descendant_names = descendant_names or set()
    sibling_names = sibling_names or set()

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
                    and ref_op_name not in descendant_names
                    and ref_op_name not in sibling_names
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


def _validate_output_keys(ops: Dict[str, "BaseOp"]) -> List[ValidationIssue]:
    """Check each ``op["key"]`` an input reads against what ``op`` returns.

    Only for producers whose output keys are known before they run
    (``BaseOp._static_outputs``: a function that returns dict literals).
    A misspelled key used to build and run, and the consumer silently got
    its default: ``show(total=m["totl"])`` printed ``total=0``. A
    misspelled input name already raises at the call site; this is the
    other half. Every op's metadata (``error``, timing, cost) is readable
    too, and ``op.get("key")`` reads an output the op may leave out.
    """
    issues = []
    for op_name, child in ops.items():
        for var, param in child.inputs.items():
            if not isinstance(param.value, Ref):
                continue
            for ref in param.value.get_all_refs():
                if ref._optional:
                    continue  # op.get("key"): the producer may leave it out
                producer = ref.raw_source
                known = getattr(producer, "_static_outputs", None)
                if known is None or ref.var in known or ref.var in OP_META_VARS:
                    continue
                func = _callable_name(producer)
                close = difflib.get_close_matches(ref.var, sorted(known), n=1)
                hint = f" — did you mean '{close[0]}'?" if close else ""
                issues.append(
                    ValidationIssue(
                        level=ValidationLevel.ERROR,
                        category="Unknown output",
                        message=(
                            f"'{ref.var}' is not an output of {func}(); outputs: "
                            f"{set(sorted(known))}{hint} (op '{op_name}', "
                            f"{_callable_name(child)}(), reads it as '{var}'). "
                            f"Read an output {func}() may leave out as "
                            f"{producer.name}.get('{ref.var}')."
                        ),
                        op_name=op_name,
                        target_name=producer.name,
                        suggestions=[
                            f"Read one of {sorted(known)} from '{producer.name}'",
                            f"Or return '{ref.var}' from {func}()",
                            f"Or, if {func}() may leave it out, read it as "
                            f"{producer.name}.get('{ref.var}')",
                        ],
                    )
                )
    return issues


def _callable_name(op: "BaseOp") -> str:
    """A function op's function name, else the op's class name."""
    fn = getattr(op, "code_fn", None)
    return getattr(fn, "__name__", None) or type(op).__name__


def _validate_concurrent_writers(
    graph: "BaseOp",
    ops: Dict[str, "BaseOp"],
    nexts: dict,
    arm_sig: Optional[Callable[[str], Dict[str, set]]],
) -> List[ValidationIssue]:
    """Two ops that may run at once both write a declared cell with no reducer.

    The cell keeps whichever value lands last, and which lands last is a
    matter of timing: probe P2 of the roadmap read ``slow`` in one run and
    ``fast`` in the next, with no error. Two writers are fine when one runs
    after the other (a path between them, along any edge), when they never
    both run (two arms of one branch, an op and its error handler), when
    the cell has a reducer, or when ``PARENT.declare(..., allow_race=True)``
    says last-write-wins is what the author wants.

    A writer is a child of the graph whose outputs push into the cell; a
    hidden loop writes what the ops in its body write.
    """
    shared = getattr(graph, "_shared_vars", None) or {}
    reducers = getattr(graph, "_reducer_vars", None) or {}
    racy_ok = getattr(graph, "_race_vars", None) or set()
    checked = {v for v in shared if v not in reducers and v not in racy_ok}
    if not checked:
        return []

    # var -> {child name: label shown in the message}
    writers: Dict[str, Dict[str, str]] = defaultdict(dict)
    for name, child in ops.items():
        for var, label in _writes_into(graph, child, checked):
            writers[var].setdefault(name, label)

    reach_cache: Dict[str, Set[str]] = {}

    def reach(name: str) -> Set[str]:
        if name not in reach_cache:
            reach_cache[name] = reachable([name], nexts)
        return reach_cache[name]

    def exclusive(p: str, q: str) -> bool:
        if arm_sig is None:
            return False
        sp, sq = arm_sig(p), arm_sig(q)
        return any(b in sq and sp[b].isdisjoint(sq[b]) for b in sp)

    issues = []
    for var in sorted(writers):
        units = sorted(writers[var])
        for i, p in enumerate(units):
            for q in units[i + 1 :]:
                if q in reach(p) or p in reach(q) or exclusive(p, q):
                    continue
                lp, lq = writers[var][p], writers[var][q]
                issues.append(
                    ValidationIssue(
                        level=ValidationLevel.ERROR,
                        category="Concurrent writers",
                        message=(
                            f"cell '{var}' of graph '{graph.name}' has concurrent writers "
                            f"{lp} and {lq}: nothing orders them, so whichever finishes "
                            f"last wins. Order them ({p} >> {q}), give the cell a reducer "
                            f"(PARENT.declare({var}=..., reducers={{'{var}': fn}})), or "
                            f"declare that last-write-wins is intended "
                            f"(PARENT.declare({var}=..., allow_race=True))."
                        ),
                        op_name=p,
                        target_name=q,
                    )
                )
    return issues


def _writes_into(graph: "BaseOp", child: "BaseOp", vars_: Set[str]):
    """``(var, label)`` for each cell in *vars_* of *graph* that *child* pushes to.

    A hidden loop's body ops keep their pushes to the graph they were
    written in, so the loop writes what they write.
    """
    for out, param in (child.outputs or {}).items():
        ref = getattr(param, "value", None)
        if isinstance(ref, Ref) and ref.raw_source is graph and ref.var in vars_:
            yield ref.var, f"'{child.name}'"
    if getattr(child, "_synthetic", False):
        for body_op in child._ops.values():
            for var, label in _writes_into(graph, body_op, vars_):
                yield var, f"{label} (in a loop)"


def _validate_ordered_reads(
    graph: "BaseOp", ops: Dict[str, "BaseOp"], prevs: dict
) -> List[ValidationIssue]:
    """An op reads another op's output, and nothing makes the producer run first.

    ``s = slow(y=f["y"])`` reads ``f`` but orders nothing: without a path
    ``f >> ... >> s``, both may start together and ``s`` gets ``f``'s output
    or not depending on timing — a missing argument, or ``None`` into a
    parameter default. Reads of ``PARENT[...]`` (graph inputs and declared
    cells) and ``SCRATCH[...]`` order nothing by design and are not checked;
    a read of another op is, along every edge kind (hard, soft, error).

    A hidden loop's body ops count as the loop op, which is what the graph's
    edges connect.
    """
    owner: Dict[int, str] = {}
    for name, child in ops.items():
        owner[id(child)] = name
        if getattr(child, "_synthetic", False):
            stack = list(child._ops.values())
            while stack:
                body = stack.pop()
                owner[id(body)] = name
                if getattr(body, "_synthetic", False):
                    stack.extend(body._ops.values())

    ancestors_cache: Dict[str, Set[str]] = {}

    def ancestors(name: str) -> Set[str]:
        if name not in ancestors_cache:
            ancestors_cache[name] = reachable([name], prevs)
        return ancestors_cache[name]

    def check(reader: str, producer_op, var: str, how: str):
        producer = owner.get(id(producer_op))
        if producer is None or producer == reader or producer in ancestors(reader):
            return None
        return ValidationIssue(
            level=ValidationLevel.ERROR,
            category="Unordered read",
            message=(
                f"op '{reader}' {how} '{producer}'['{var}'] in graph '{graph.name}', but "
                f"nothing makes '{producer}' run first: both may start together, and "
                f"'{reader}' gets the value or not depending on timing. A read orders "
                f"nothing; draw the edge ({producer} >> {reader}, or "
                f"START >> {producer} >> {reader}). To read whatever value is there "
                f"at the time, share it through a declared cell: "
                f"PARENT.declare({var}=None), {producer}['{var}'] >> PARENT['{var}'], "
                f"and read PARENT['{var}']."
            ),
            op_name=reader,
            target_name=producer,
        )

    issues = []
    for name, child in ops.items():
        for param in (child.inputs or {}).values():
            if isinstance(param.value, Ref):
                for ref in param.value.get_all_refs():
                    issue = check(name, ref.raw_source, ref.var, "reads")
                    if issue is not None:
                        issues.append(issue)
        # The push form, producer["out"] >> consumer["in"], wires the same read.
        for out, param in (child.outputs or {}).items():
            ref = getattr(param, "value", None)
            consumer = owner.get(id(ref.raw_source)) if isinstance(ref, Ref) else None
            if consumer is not None:
                issue = check(consumer, child, out, "is fed")
                if issue is not None:
                    issues.append(issue)
    return issues
