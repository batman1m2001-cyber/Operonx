"""WhileOp — conditional loop that repeats until a condition is met."""

from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from hush.core.configs.op_config import OpType
from hush.core.exceptions import ConditionError
from hush.core.loggings import LOGGER
from hush.core.ops.base import shorthand
from hush.core.ops.iteration.base import BaseIterationOp, get_iter_context, split_iter_kwargs
from hush.core.utils.common import Param, extract_condition_variables

if TYPE_CHECKING:
    from hush.core.states import MemoryState


class WhileOp(BaseIterationOp):
    """Conditional loop op — repeats until ``until`` expression evaluates to True.

    Use when the number of iterations is unknown upfront (e.g. retry loops,
    agentic reasoning). A ``max_iterations`` safety limit prevents runaway loops.

    Inputs:
        Initial loop state variables (e.g. ``counter=0``). Child ops update
        these via output mappings to ``PARENT``.
        ``until`` (str): Python expression evaluated each iteration.
        ``max_iterations`` (int): Safety limit. Default: 100.

    Outputs:
        Final loop state plus ``iteration_metrics`` with
        ``max_iterations_reached`` and ``stopped_by_condition`` flags.

    Example::

        with WhileOp(inputs={"counter": 0}, until="counter >= 5") as loop:
            step = increment(counter=PARENT["counter"])
            step["new_counter"] >> PARENT["counter"]
            START >> step >> END
    """

    type: OpType = "while"

    __slots__ = ["_max_iterations", "_until", "_compiled_condition"]

    def __init__(self, until: Optional[str] = None, max_iterations: int = 100, **kwargs):
        """Initialize WhileOp.

        Args:
            until: String expression evaluated each iteration.
                When evaluates to True, loop stops.
            max_iterations: Max iterations to prevent infinite loops. Default 100.
        """
        super().__init__(**kwargs)

        self._max_iterations = max_iterations
        self._until = until
        self._compiled_condition = self._compile_condition(until) if until else None

    def _compile_condition(self, condition: str):
        """Compile until condition for performance."""
        try:
            return compile(condition, f"<until: {condition}>", "eval")
        except SyntaxError as e:
            raise ConditionError(
                message="Invalid until expression syntax",
                condition=condition,
                phase="compile",
                original_error=e,
            ) from e

    def _evaluate_until(self, inputs: Dict[str, Any], iteration: Optional[int] = None) -> bool:
        """Evaluate until condition with current inputs.

        Args:
            inputs: Current input values.
            iteration: Current iteration number (for error context).

        Returns:
            True if loop should stop, False to continue.
        """
        if self._compiled_condition is None:
            return False

        try:
            result = eval(self._compiled_condition, {"__builtins__": {}}, inputs)
            return bool(result)
        except Exception as e:
            error = ConditionError(
                message="Until condition evaluation failed",
                condition=self._until,
                inputs=inputs,
                iteration=iteration,
                phase="eval",
                original_error=e,
            )
            LOGGER.warning(str(error))
            return False

    def _post_build(self):
        """Setup inputs/outputs from graph after build."""
        # Normalize raw inputs (resolve PARENT refs)
        normalized_inputs = self._normalize_params(self._raw_inputs)
        user_inputs = {k: v for k, v in normalized_inputs.items()}

        existing_outputs = self.outputs or {}

        # Start with graph's inputs/outputs
        parsed_inputs = {
            k: Param(
                type=v.type,
                required=v.required,
                default=v.default,
                description=v.description,
                value=v.value,
            )
            for k, v in (self.inputs or {}).items()
        }
        parsed_outputs = {
            k: Param(
                type=v.type,
                required=v.required,
                default=v.default,
                description=v.description,
                value=v.value,
            )
            for k, v in (self.outputs or {}).items()
        }

        # Add variables from until expression to inputs
        if self._until:
            for var_name in extract_condition_variables(self._until):
                if var_name not in parsed_inputs:
                    parsed_inputs[var_name] = Param(type=Any, required=False)

        if "iteration_metrics" not in parsed_outputs:
            parsed_outputs["iteration_metrics"] = Param(type=Dict, required=False)

        self.inputs = self._merge_params(parsed_inputs, user_inputs)
        self.outputs = parsed_outputs

        for key, existing_param in existing_outputs.items():
            if key in self.outputs and existing_param.value is not None:
                self.outputs[key].value = existing_param.value

    async def _execute(
        self,
        state: "MemoryState",
        context_id: Optional[str],
        parent_context: Optional[str],
        request_id: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Execute while loop until condition is True or max_iterations reached."""
        _inputs = self.get_inputs(state, context_id, parent_context)
        step_inputs = _inputs
        step_count = 0

        should_stop = self._evaluate_until(step_inputs, iteration=0)

        ctx_prefix = (context_id + ".") if context_id else ""
        while not should_stop and step_count < self._max_iterations:
            step_context = get_iter_context(ctx_prefix, step_count)

            for var_name, value in step_inputs.items():
                state[self.full_name, var_name, step_context] = value

            _outputs = await self._run_graph(state, step_context, step_context)

            step_inputs = {**step_inputs, **_outputs}
            step_count += 1

            should_stop = self._evaluate_until(step_inputs, iteration=step_count)

        if step_count >= self._max_iterations and not should_stop:
            LOGGER.warning(
                "[title]\\[%s][/title] WhileOp [highlight]%s[/highlight]: max_iterations [muted](%s)[/muted] reached.",
                request_id,
                self.full_name,
                self._max_iterations,
            )

        iteration_metrics = {
            "total_iterations": step_count,
            "success_count": step_count,
            "error_count": 0,
            "max_iterations_reached": step_count >= self._max_iterations,
            "stopped_by_condition": should_stop,
        }

        _outputs["iteration_metrics"] = iteration_metrics

        return _inputs, _outputs

    @shorthand
    def of(cls, **kwargs) -> "WhileOp":
        """Create a WhileOp with flat kwargs.

        Example::

            with WhileOp.of(counter=0, until="counter >= 5") as loop:
                step = process(counter=PARENT["counter"])
                step["new_counter"] >> PARENT["counter"]
                START >> step >> END
        """
        inputs, init_kwargs = split_iter_kwargs(kwargs)
        return cls(inputs=inputs, **init_kwargs)

    def serialize(self) -> dict:
        """Serialize WhileOp for Rust backend."""
        base = super().serialize()
        base["until"] = self._until
        base["max_iterations"] = self._max_iterations
        return base

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return subclass-specific metadata."""
        return {"max_iterations": self._max_iterations, "until": self._until}
