"""While loop node to iterate while condition is true."""

from typing import Dict, Any, List, Optional, Tuple, TYPE_CHECKING

from hush.core.configs.node_config import NodeType
from hush.core.nodes.iteration.base import BaseIterationNode, get_iter_context, split_iter_kwargs
from hush.core.utils.common import Param, extract_condition_variables
from hush.core.loggings import LOGGER

if TYPE_CHECKING:
    from hush.core.states import MemoryState


class WhileLoopNode(BaseIterationNode):
    """Node that iterates while condition is true.

    Loop continues while `stop_condition` evaluates to False.
    When `stop_condition` becomes True, loop stops.

    Example:
        with WhileLoopNode(
            name="loop",
            inputs={"counter": 0},
            stop_condition="counter >= 5"
        ) as loop:
            node = process(
                inputs={"counter": PARENT["counter"]},
                outputs={"new_counter": PARENT["counter"]}
            )
            START >> node >> END
    """

    type: NodeType = "while"

    __slots__ = ['_max_iterations', '_stop_condition', '_compiled_condition']

    def __init__(
        self,
        stop_condition: Optional[str] = None,
        max_iterations: int = 100,
        **kwargs
    ):
        """Initialize WhileLoopNode.

        Args:
            stop_condition: String expression evaluated each iteration.
                When evaluates to True, loop stops.
            max_iterations: Max iterations to prevent infinite loops. Default 100.
        """
        super().__init__(**kwargs)

        self._max_iterations = max_iterations
        self._stop_condition = stop_condition
        self._compiled_condition = self._compile_condition(stop_condition) if stop_condition else None

    def _compile_condition(self, condition: str):
        """Compile stop condition for performance."""
        try:
            return compile(condition, f'<stop_condition: {condition}>', 'eval')
        except SyntaxError as e:
            LOGGER.error("Invalid stop_condition syntax [str]'%s'[/str]: %s", condition, e)
            raise ValueError(f"Invalid stop_condition syntax: {condition}") from e

    def _evaluate_stop_condition(self, inputs: Dict[str, Any]) -> bool:
        """Evaluate stop condition with current inputs.

        Returns:
            True if loop should stop, False to continue.
        """
        if self._compiled_condition is None:
            return False

        try:
            result = eval(self._compiled_condition, {"__builtins__": {}}, inputs)
            return bool(result)
        except Exception as e:
            LOGGER.error("Error evaluating stop_condition [str]'%s'[/str]: %s", self._stop_condition, e)
            return False

    def _post_build(self):
        """Setup inputs/outputs from graph after build."""
        # Normalize raw inputs (resolve PARENT refs)
        normalized_inputs = self._normalize_params(self._raw_inputs)
        user_inputs = {k: v for k, v in normalized_inputs.items()}

        existing_outputs = self.outputs or {}

        # Start with graph's inputs/outputs
        parsed_inputs = {
            k: Param(type=v.type, required=v.required, default=v.default,
                     description=v.description, value=v.value)
            for k, v in (self.inputs or {}).items()
        }
        parsed_outputs = {
            k: Param(type=v.type, required=v.required, default=v.default,
                     description=v.description, value=v.value)
            for k, v in (self.outputs or {}).items()
        }

        # Add variables from stop_condition to inputs
        if self._stop_condition:
            for var_name in extract_condition_variables(self._stop_condition):
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
        state: 'MemoryState',
        context_id: Optional[str],
        parent_context: Optional[str],
        request_id: str
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Execute while loop until stop_condition is True or max_iterations reached."""
        _inputs = self.get_inputs(state, context_id, parent_context)
        step_inputs = _inputs
        step_count = 0

        should_stop = self._evaluate_stop_condition(step_inputs)

        ctx_prefix = (context_id + ".") if context_id else ""
        while not should_stop and step_count < self._max_iterations:
            step_context = get_iter_context(ctx_prefix, step_count)

            for var_name, value in step_inputs.items():
                state[self.full_name, var_name, step_context] = value

            _outputs = await self._run_graph(state, step_context, step_context)

            step_inputs = {**step_inputs, **_outputs}
            step_count += 1

            should_stop = self._evaluate_stop_condition(step_inputs)

        if step_count >= self._max_iterations and not should_stop:
            LOGGER.warning(
                "[title]\\[%s][/title] WhileLoopNode [highlight]%s[/highlight]: max_iterations [muted](%s)[/muted] reached.",
                request_id, self.full_name, self._max_iterations
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

    def specific_metadata(self) -> Dict[str, Any]:
        """Return subclass-specific metadata."""
        return {
            "max_iterations": self._max_iterations,
            "stop_condition": self._stop_condition
        }


def while_(**kwargs) -> WhileLoopNode:
    """Shorthand to create a WhileLoopNode with flat kwargs.

    Example:
        with while_(counter=0, stop_condition="counter >= 5") as loop:
            node = process(counter=PARENT["counter"])
            node["new_counter"] >> PARENT["counter"]
            START >> node >> END
    """
    _skip_auto_name = True  # noqa: F841
    inputs, init_kwargs = split_iter_kwargs(kwargs)
    return WhileLoopNode(inputs=inputs, **init_kwargs)