"""BranchOp — conditional routing op for workflow control flow."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from operonx.core.configs.op_config import OpType
from operonx.core.exceptions import BranchError
from operonx.core.loggings import LOGGER
from operonx.core.ops.base import BaseOp
from operonx.core.states.ref import Ref
from operonx.core.utils.auto_name import auto_name, register_skip
from operonx.core.utils.common import Param
from operonx.core.utils.context import get_current

if TYPE_CHECKING:
    from operonx.core.states import MemoryState


class BranchOp(BaseOp):
    """Op that evaluates conditions and routes execution to different targets.

    Conditions are ``Ref`` objects with comparison operators. The first matching
    condition determines the target. An optional ``anchor`` input overrides all
    conditions. Use soft edges (``>>~``) to connect branch targets to a merge op.

    Inputs:
        anchor (str, optional): Hard-coded target name that overrides conditions.
        <var> (any): Variables referenced in condition Refs (auto-extracted).

    Outputs:
        target (str): Name of the selected target op.
        matched (str): Description of which condition matched.

    Example::

        router = if_(PARENT["score"] >= 90, "excellent").else_("fail")
        START >> router >> ~excellent >> merge >> END
        router >> ~fail >> merge
    """

    type: OpType = "branch"

    show_keys_default = ("target",)

    __slots__ = [
        "given_candidates",
        "default",
        "cases",
        "_case_descriptions",
        "_input_keys",
        "condition_ops",
        "max_iterations",
    ]

    def __init__(
        self,
        cases: Optional[List[Tuple[Ref, str]]] = None,
        candidates: Optional[List[str]] = None,
        default: Optional[str] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        max_iterations: Optional[int] = None,
        **kwargs,
    ):
        # Parse inputs/outputs from cases
        parsed_inputs, parsed_outputs, input_keys = self._parse_cases(cases or [])

        # Call super().__init__ without inputs/outputs
        super().__init__(**kwargs)

        # Merge parsed schema with user-provided
        self._init_io(parsed_inputs, parsed_outputs, inputs, outputs)

        self.default = default.name if isinstance(default, BaseOp) else default
        self.given_candidates = candidates
        self.cases = cases or []
        self._case_descriptions = [ref.describe() for ref, _ in self.cases]
        #: input name -> the ``(source, var)`` key its value has when a
        #: condition runs. See `_evaluate_conditions`.
        self._input_keys: Dict[str, Tuple[Any, str]] = input_keys
        #: Predicate ops this branch tests — populated by `Branch._build()`
        #: when a condition is an op rather than a Ref. Empty for the
        #: classic `if_(op["field"], ...)` form.
        self.condition_ops: List[BaseOp] = []
        #: The iteration cap of the loop this branch closes, when one of
        #: its arms is the loop's back-edge. None keeps the default cap;
        #: the cycle rewrite reads it (see `cycle_rewrite.loop_cap`).
        self.max_iterations = _checked_max_iterations(max_iterations)

        self._set_core(self._create_core_function())

    def _parse_cases(self, cases: List[Tuple[Ref, str]]) -> tuple:
        """Parse inputs/outputs from cases.

        Every Ref a condition reads becomes an input: the one it starts
        from and each one among its operands (``a >= b``, ``a & b``). They
        are told apart by ``(source, var)``, not by var alone, so
        ``(a["n"] > 5) & (b["n"] < 3)`` reads two values. The first keeps
        the plain var name (``branch(n=...)``); a later one from another
        source gets the source's name appended.

        Args:
            cases: List of (condition_ref, target) tuples

        Returns:
            Tuple[Dict[str, Param], Dict[str, Param], Dict[str, tuple]]:
            (inputs, outputs, input name -> ``(source, var)``)
        """
        # Inputs: anchor + variables from conditions
        inputs = {"anchor": Param(type=str, default=None)}
        input_keys: Dict[str, Tuple[Any, str]] = {}
        named: Dict[Tuple[Any, str], str] = {}

        for ref, target in cases:
            for base_ref in ref.get_all_refs():
                key = base_ref._ctx_key()
                if key in named:
                    continue
                name = base_ref.var
                if name in inputs:
                    source = base_ref.raw_source
                    label = getattr(source, "name", source)
                    label = "parent" if label == "__PARENT__" else str(label).rsplit(".", 1)[-1]
                    name = f"{base_ref.var}_{label}"
                    n = 2
                    while name in inputs:
                        name = f"{base_ref.var}_{label}_{n}"
                        n += 1
                named[key] = name
                input_keys[name] = key
                inputs[name] = Param(required=True, value=base_ref)

        # Outputs
        outputs = {
            "target": Param(type=str, required=True),
            "matched": Param(type=str),
            "__branch_target__": Param(type=str),
        }

        return inputs, outputs, input_keys

    @property
    def candidates(self) -> List[str]:
        """List of possible target op names."""
        if self.given_candidates:
            return self.given_candidates

        targets = [target for _, target in self.cases]

        if self.default:
            return targets + [self.default]
        else:
            return targets

    def _create_core_function(self):
        """Create the optimised core evaluation function."""

        def core(**inputs) -> Dict[str, str]:
            anchor = inputs.get("anchor")
            if anchor:
                return {"target": anchor, "matched": "anchor", "__branch_target__": anchor}

            target, matched = self._evaluate_conditions(inputs)
            return {"target": target, "matched": matched, "__branch_target__": target}

        return core

    def _evaluate_conditions(self, inputs: Dict[str, Any]) -> tuple:
        """Evaluate all conditions and return the first match."""
        safe_inputs = dict(inputs)
        # A Ref reads its value by (source, var) — see `Ref._resolve` —
        # so re-key the inputs from their names to those keys.
        context = {key: safe_inputs.get(name) for name, key in self._input_keys.items()}

        for i, (ref, target) in enumerate(self.cases):
            try:
                result = ref._resolve(context)

                if result:
                    condition_desc = self._case_descriptions[i]
                    LOGGER.debug(
                        "Điều kiện [str]'%s'[/str] khớp, định tuyến đến [highlight]%s[/highlight]",
                        condition_desc,
                        target,
                    )
                    return target, condition_desc

            except Exception as e:
                error = BranchError(
                    message=f"Condition evaluation failed for '{ref.var}'",
                    condition=str(ref),
                    inputs=safe_inputs,
                    candidates=self.candidates,
                    original_error=e,
                )
                LOGGER.warning(str(error))
                continue

        if self.default:
            LOGGER.debug(
                "Không có điều kiện khớp, sử dụng target mặc định [highlight]%s[/highlight]",
                self.default,
            )
            return self.default, "default"
        else:
            LOGGER.warning("Không có điều kiện khớp và không có target mặc định")
            return None, None

    def get_target(self, state: "MemoryState", context_id: Optional[str] = None) -> Optional[str]:
        """Get the routed target from state."""
        return state[self.full_name, "target", context_id]

    def serialize(self) -> dict:
        """Serialize branch op with its conditions."""
        base = super().serialize()
        base.update(
            {
                "cases": [
                    {"condition": ref.serialize(), "target": target} for ref, target in self.cases
                ],
                "default": self.default,
                "candidates": self.given_candidates,
            }
        )
        return base

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return subclass-specific metadata."""
        return {
            "cases": [(str(ref), target) for ref, target in self.cases],
            "default_target": self.default,
            "candidates": self.candidates,
            "num_conditions": len(self.cases),
        }


def _checked_max_iterations(value: Optional[int]) -> Optional[int]:
    """``value`` if it is a usable loop cap (an int of at least 1), else raise."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            f"max_iterations must be a whole number of at least 1, got {value!r}. "
            f"It caps the loop this branch closes: if_(cond, END, max_iterations=50)."
        )
    return value


def _rename_op(op: BaseOp, new_name: str) -> None:
    """Re-key an op in its graph. Only safe before any edge names it."""
    graph = getattr(op, "parent", None)
    ops = getattr(graph, "_ops", None)
    if ops is None or ops.get(op.name) is not op:
        op.name = new_name
        return
    if new_name in ops:
        n = 2
        while f"{new_name}_{n}" in ops:
            n += 1
        new_name = f"{new_name}_{n}"
    del ops[op.name]
    op.name = new_name
    ops[new_name] = op


def _entry_of(graph, source: str, target: BaseOp) -> str:
    """The op a caller must route to in order to reach *target*.

    Normally *target* itself. But a branch target can be another branch
    whose condition is an op: that predicate is registered and unwired, so
    routing straight at the branch leaves the predicate unable to run — the
    inner branch then reads an unset cell and every call takes its else
    arm, silently. Route at the predicate instead; it already has its own
    edge onward to the branch.

    A branch selects its successor *by name*, so the caller's recorded
    target has to change with the edge. Returning the name keeps the two
    in step — see `_build`, where both come from this one call.
    """
    predicates = getattr(target, "condition_ops", None)
    if not predicates:
        return target.name
    prevs = getattr(graph, "prevs", None)
    unwired = [
        p
        for p in predicates
        if p.name != source
        and not getattr(p, "start", False)
        and (prevs is None or not prevs.get(p.name))
    ]
    if len(unwired) != 1:
        # Zero: already reachable. More than one: no single entry exists, so
        # say so rather than pick one and route on a half-evaluated branch.
        if len(unwired) > 1:
            raise ValueError(
                f"branch '{target.name}' tests {len(unwired)} ops and is itself a "
                f"branch target; there is no single op to route through. Assign its "
                f"predicates to names and wire them before the branch."
            )
        return target.name
    return unwired[0].name


def _as_condition_ref(condition: Union[Ref, BaseOp]) -> Tuple[Ref, Optional[BaseOp]]:
    """Normalise a branch condition to ``(ref, predicate_op)``.

    A ``Ref`` passes through with no predicate — that is the original form,
    ``if_(op["field"] == x, target)``.

    A ``BaseOp`` is a predicate: the op runs, and the branch tests its
    single output. It must declare exactly one, because ``if_(route_check,
    ...)`` on an op returning ``{is_heavy_kw, keyword}`` has no defensible
    answer — refuse it here rather than pick one and be wrong on a call
    nobody is watching.
    """
    if isinstance(condition, Ref):
        return condition, None

    if not isinstance(condition, BaseOp):
        raise TypeError(
            f"branch condition must be a Ref or an op, got {type(condition).__name__}. "
            f"Write if_(op['field'], target) or if_(predicate_op(...), target)."
        )

    outs = [k for k in (condition.outputs or {}) if not k.startswith("__")]
    if len(outs) != 1:
        detail = f"declares {len(outs)}: {outs}" if outs else "declares none"
        raise ValueError(
            f"if_({condition.name}, ...) needs an op with exactly one output; "
            f"{condition.name} {detail}. Either name the field — "
            f"if_({condition.name}['<field>'], target) — or give the function a "
            f"scalar return annotation so it declares one."
        )

    return condition[outs[0]], condition


class Branch:
    """Fluent builder for creating a BranchOp.

    Two usage flavors — both supported, both idiomatic:

    **Inline form (recommended for common if/else)** — pass op *instances* as
    targets. The Branch auto-wires ``branch >> target`` edges at build time
    so you never write them yourself, and the branch can drop right into a
    ``>>`` chain::

        START >> source >> if_(source["kind"] == "audio", asr).else_(skip_stt)
        asr >> denoise >> picker
        skip_stt >> picker

    Auto-name resolves to the LHS if there is one (``stt_route = if_(...)``);
    inline, to a per-graph counter, ``route_1``, ``route_2``.

    **Named form (for forward refs or a shared branch node)** — pass op
    *names* as strings. No auto-wiring; you wire ``branch >> target``
    yourself as before::

        router = (if_(PARENT["score"] >= 90, "excellent")
                  .if_(PARENT["score"] >= 70, "good")
                  .else_("fail"))
        # ...
        router >> excellent >> merge
        router >> good      >> merge
        router >> fail      >> merge

    Mixed is allowed — string targets skip auto-wiring, op-instance targets
    get auto-wired.
    """

    __slots__ = ("_name", "_cases", "_default", "_inputs", "_kwargs")
    _is_operonx_builder = True

    def __init__(self, name: Optional[str] = None, **kwargs):
        """Initialise the builder.

        Args:
            name: Op name. If None, auto-inferred from the variable name.
        """
        self._name = name
        # cases stores the ORIGINAL target (op instance or string), not just
        # the name, so ``_build()`` can auto-wire op-instance targets.
        self._cases: List[Tuple[Ref, Any]] = []
        self._default: Any = None
        self._inputs: Dict[str, Any] = {}
        self._kwargs = kwargs

    def if_(self, condition: Union[Ref, BaseOp], target: Union[str, BaseOp]) -> "Branch":
        """Add a condition–target case.

        Args:
            condition: Ref with comparison (e.g., ``PARENT["score"] >= 90``).
            target: Target op instance (enables auto-wiring) or op name string.

        Returns:
            self for chaining.
        """
        self._cases.append((condition, target))
        return self

    @register_skip
    def else_(self, target: Union[str, BaseOp]) -> "BranchOp":
        """Set default target and build the BranchOp.

        Args:
            target: Fallback op instance or name string.

        Returns:
            The constructed BranchOp.
        """
        self._default = target
        return self._build()

    @register_skip
    def build(self) -> "BranchOp":
        """Build the BranchOp without a default target.

        Returns:
            The constructed BranchOp.
        """
        return self._build()

    @register_skip
    def _build(self) -> "BranchOp":
        """Internal build method.

        Resolves the branch's own name (LHS via auto_name → semantic
        fallback), constructs the BranchOp with string-name cases, then
        auto-wires ``branch >> target`` for every op-instance target found
        in cases/default (skipped for string targets — those are forward
        references the user wires manually).
        """
        # Normalise conditions first: a Ref passes through, an op-condition
        # becomes a Ref on its single output and hands back the predicate op
        # so it can be wired below.
        predicates: List[BaseOp] = []
        normalised: List[Tuple[Ref, Any]] = []
        for cond, target in self._cases:
            ref, predicate = _as_condition_ref(cond)
            if predicate is not None:
                predicates.append(predicate)
            normalised.append((ref, target))
        self._cases = normalised

        # Resolve target names for BranchOp constructor (accepts strings only).
        case_names: List[Tuple[Ref, str]] = [
            (cond, t.name if isinstance(t, BaseOp) else t) for cond, t in self._cases
        ]
        default_name: Optional[str] = None
        if self._default is not None:
            default_name = (
                self._default.name if isinstance(self._default, BaseOp) else self._default
            )

        # Name resolution: explicit > the variable it is assigned to > a
        # per-graph counter, ``route_1``. Inline
        # (``source >> if_(...).else_(...)``) there is no variable.
        lhs = auto_name()
        # A predicate built inline as an argument runs BEFORE the branch, so
        # on `inner = if_(is_small(...), a).else_(b)` it is the predicate that
        # auto_name hands `inner` to. The reader's `inner` means the branch;
        # give the predicate its function's name back.
        for predicate in predicates:
            if self._name or predicate._name_hint in (None, predicate.name):
                continue
            if lhs == predicate.name:
                _rename_op(predicate, predicate._name_hint)

        name = self._name
        if not name:
            g = get_current()
            taken = g._ops if g is not None else {}
            if lhs and lhs not in taken:
                name = lhs
            else:
                name = f"route_{1 + sum(1 for op in taken.values() if op.type == 'branch')}"

        # Inputs come from the conditions — `BranchOp._parse_cases`.
        branch = BranchOp(
            name=name,
            cases=case_names,
            default=default_name,
            **self._kwargs,
        )
        # Ops whose output this branch tests. `__rshift__` reads these to
        # route an incoming edge through them — see `condition_ops`.
        branch.condition_ops = predicates

        # Auto-wire outgoing edges for op-instance targets. String targets are
        # forward refs — user still writes ``branch >> target`` manually for
        # those. This is the whole point of the inline form.
        current_graph = get_current()
        if current_graph is not None and hasattr(current_graph, "add_edge"):
            # A predicate has to finish before the branch can read it. This
            # is a normal edge, not a condition edge: the branch waits.
            for predicate in predicates:
                current_graph.add_edge(predicate.name, branch.name, type="normal")
            for i, (_cond, target) in enumerate(self._cases):
                if isinstance(target, BaseOp):
                    entry = _entry_of(current_graph, branch.name, target)
                    current_graph.add_edge(branch.name, entry, type="condition")
                    branch.cases[i] = (branch.cases[i][0], entry)
            if isinstance(self._default, BaseOp):
                entry = _entry_of(current_graph, branch.name, self._default)
                current_graph.add_edge(branch.name, entry, type="condition")
                branch.default = entry

        return branch


def if_(
    condition: Union[Ref, BaseOp],
    target: Union[str, BaseOp],
    *,
    max_iterations: Optional[int] = None,
) -> Branch:
    """Start a branch declaration with the first condition.

    Args:
        condition: What to test — a Ref comparison or a single-output op.
        target: The op (or op name) to route to when it holds.
        max_iterations: When one of this branch's arms loops back to an
            earlier op, the most iterations that loop may run (default
            1000). A loop that reaches it stops, records
            ``LoopLimitExceeded`` in ``$errors``, and runs nothing after it.
            Refused at build on a branch that closes no loop.

    Example (inline, auto-wired)::

        START >> source >> if_(cond, asr).else_(skip_stt)

    Example (named, string targets, wire manually)::

        router = if_(PARENT["score"] >= 90, "excellent").else_("fail")
        router >> excellent >> merge
        router >> fail      >> merge

    Example (a loop capped at 50 iterations)::

        START >> s >> if_(s["done"] == True, END, max_iterations=50).else_(s)
    """
    if max_iterations is None:
        return Branch().if_(condition, target)
    return Branch(max_iterations=_checked_max_iterations(max_iterations)).if_(condition, target)
