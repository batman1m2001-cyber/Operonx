"""BranchOp — conditional routing op for workflow control flow."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from operonx.core.configs.op_config import OpType
from operonx.core.exceptions import BranchError
from operonx.core.loggings import LOGGER
from operonx.core.ops.base import BaseOp
from operonx.core.states.ref import Ref
from operonx.core.utils.auto_name import register_skip
from operonx.core.utils.common import Param

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

    __slots__ = [
        "given_candidates",
        "default",
        "cases",
        "_case_descriptions",
    ]

    def __init__(
        self,
        cases: Optional[List[Tuple[Ref, str]]] = None,
        candidates: Optional[List[str]] = None,
        default: Optional[str] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs,
    ):
        # Parse inputs/outputs from cases
        parsed_inputs, parsed_outputs = self._parse_cases(cases or [])

        # Call super().__init__ without inputs/outputs
        super().__init__(**kwargs)

        # Merge parsed schema with user-provided
        self._init_io(parsed_inputs, parsed_outputs, inputs, outputs)

        self.default = default.name if isinstance(default, BaseOp) else default
        self.given_candidates = candidates
        self.cases = cases or []
        self._case_descriptions = [ref.describe() for ref, _ in self.cases]

        self._set_core(self._create_core_function())

    def _parse_cases(self, cases: List[Tuple[Ref, str]]) -> tuple:
        """Parse inputs/outputs from cases.

        Args:
            cases: List of (condition_ref, target) tuples

        Returns:
            Tuple[Dict[str, Param], Dict[str, Param]]: (inputs, outputs)
        """
        # Inputs: anchor + variables from conditions
        inputs = {"anchor": Param(type=str, default=None)}

        for ref, target in cases:
            # Use get_all_vars() to extract all variables from compound refs
            # e.g., (PARENT["a"] > 10) & (PARENT["b"] == "x") -> {"a", "b"}
            for var_name in ref.get_all_vars():
                if var_name not in inputs:
                    # Create base Ref without ops for input resolution
                    base_ref = Ref(ref.raw_source, var_name)
                    inputs[var_name] = Param(required=True, value=base_ref)

        # Outputs
        outputs = {
            "target": Param(type=str, required=True),
            "matched": Param(type=str),
            "__branch_target__": Param(type=str),
        }

        return inputs, outputs

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

        for i, (ref, target) in enumerate(self.cases):
            try:
                value = safe_inputs.get(ref.var)
                # Pass context for compound boolean operations (& and |)
                result = ref.execute(value, context=safe_inputs)

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
        """Serialize branch op with conditions for Rust backend."""
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


class Branch:
    """Fluent builder for creating a BranchOp.

    Example::

        router = (if_(PARENT["score"] >= 90, "excellent")
                  .if_(PARENT["score"] >= 70, "good")
                  .else_("fail"))
    """

    __slots__ = ("_name", "_cases", "_default", "_inputs", "_kwargs")
    _is_operonx_builder = True

    def __init__(self, name: Optional[str] = None, **kwargs):
        """Initialise the builder.

        Args:
            name: Op name. If None, auto-inferred from the variable name.
        """
        self._name = name
        self._cases: List[Tuple[Ref, str]] = []
        self._default: Optional[str] = None
        self._inputs: Dict[str, Any] = {}
        self._kwargs = kwargs

    def if_(self, condition: Ref, target: Union[str, BaseOp]) -> "Branch":
        """Add a condition–target case.

        Args:
            condition: Ref with comparison (e.g., ``PARENT["score"] >= 90``).
            target: Target op or op name.

        Returns:
            self for chaining.
        """
        target_name = target.name if hasattr(target, "name") else target
        self._cases.append((condition, target_name))
        return self

    @register_skip
    def else_(self, target: Union[str, BaseOp]) -> "BranchOp":
        """Set default target and build the BranchOp.

        Args:
            target: Fallback target when no condition matches.

        Returns:
            The constructed BranchOp.
        """
        self._default = target.name if hasattr(target, "name") else target
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
        """Internal build method."""
        all_inputs = {}

        for condition_ref, target in self._cases:
            var_name = condition_ref.var
            if var_name not in all_inputs:
                base_ref = Ref(condition_ref.raw_source, var_name)
                all_inputs[var_name] = base_ref

        return BranchOp(
            name=self._name,
            cases=self._cases,
            default=self._default,
            inputs=all_inputs,
            **self._kwargs,
        )


def if_(condition: Ref, target: Union[str, BaseOp]) -> Branch:
    """Start a branch declaration with the first condition.

    Example::

        router = if_(PARENT["score"] >= 90, "excellent").else_("fail")
    """
    return Branch().if_(condition, target)
