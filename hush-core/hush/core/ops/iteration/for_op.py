"""ForOp — sequential iteration op that processes items one at a time."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from hush.core.configs.op_config import OpType
from hush.core.exceptions import IterationError
from hush.core.loggings import LOGGER
from hush.core.ops.base import shorthand
from hush.core.ops.iteration.base import BaseIterationOp, get_iter_context, split_iter_kwargs

if TYPE_CHECKING:
    from hush.core.states import MemoryState


class ForOp(BaseIterationOp):
    """Sequential iteration op — processes items one at a time, in order.

    Use when iterations depend on prior results, execution order matters,
    or memory constraints require one-at-a-time processing.

    Inputs:
        Wrap iterable sources with ``Each()``; all other inputs are broadcast.

    Outputs:
        Column-oriented lists (one list per output key) plus
        ``iteration_metrics`` with counts and timing.

    Example::

        with ForOp(inputs={"x": Each([1, 2, 3]), "m": 10}) as loop:
            step = calc(x=PARENT["x"], m=PARENT["m"])
            START >> step >> END
        # results: {"result": [10, 20, 30], "iteration_metrics": {...}}
    """

    type: OpType = "for"

    __slots__ = ["_fail_fast"]

    def __init__(self, inputs: Optional[Dict[str, Any]] = None, fail_fast: bool = False, **kwargs):
        """Initialize ForOp.

        Args:
            inputs: Dict mapping variable names to values or Each(source).
            fail_fast: If True, raise IterationError on first failure instead of continuing.
        """
        super().__init__(inputs=inputs, **kwargs)
        self._fail_fast = fail_fast

    def _post_build(self):
        """Setup inputs/outputs after graph is built."""
        self._normalize_iteration_io()

    async def _execute(
        self,
        state: "MemoryState",
        context_id: Optional[str],
        parent_context: Optional[str],
        request_id: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Execute for loop through iteration data sequentially."""
        # Resolve each and broadcast values using parent_context
        each_values = self._resolve_values(self._each, state, parent_context)
        broadcast_values = self._resolve_values(self._broadcast_inputs, state, parent_context)

        # Build iteration data
        iteration_data = self._build_iteration_data(each_values, broadcast_values)
        _inputs = {**each_values, **broadcast_values}

        if not iteration_data:
            LOGGER.warning(
                "[title]\\[%s][/title] ForOp [highlight]%s[/highlight]: no iteration data.",
                request_id,
                self.full_name,
            )

        # Execute iterations sequentially
        final_results: List[Dict[str, Any]] = []
        success_count = 0

        ctx_prefix = (context_id + ".") if context_id else ""
        for i, loop_data in enumerate(iteration_data):
            iter_context = get_iter_context(ctx_prefix, i)

            try:
                for var_name, value in loop_data.items():
                    state[self.full_name, var_name, iter_context] = value

                result = await self._run_graph(state, iter_context, iter_context)
                final_results.append(result)
                success_count += 1
            except Exception as e:
                error = IterationError(
                    message=f"Iteration {i} failed",
                    iteration_index=i,
                    loop_data=loop_data,
                    total_iterations=len(iteration_data),
                    op_type="for",
                    original_error=e,
                )
                if self._fail_fast:
                    raise error from e
                LOGGER.warning(str(error))
                final_results.append({"error": str(e), "error_type": type(e).__name__})

        error_count = len(iteration_data) - success_count

        # Build metrics
        iteration_metrics = {
            "total_iterations": len(iteration_data),
            "success_count": success_count,
            "error_count": error_count,
        }

        if iteration_data and error_count / len(iteration_data) > 0.1:
            LOGGER.warning(
                "[title]\\[%s][/title] ForOp [highlight]%s[/highlight]: high error rate [muted](%s)[/muted].",
                request_id,
                self.full_name,
                f"{error_count / len(iteration_data):.1%}",
            )

        # Transpose results to column-oriented format
        output_keys = [k for k in self.outputs.keys() if k != "iteration_metrics"]
        _outputs = {key: [r.get(key) for r in final_results] for key in output_keys}
        _outputs["iteration_metrics"] = iteration_metrics

        return _inputs, _outputs

    @shorthand
    def of(cls, **kwargs) -> "ForOp":
        """Create a ForOp with flat kwargs.

        Example::

            with ForOp.of(x=Each([1, 2, 3]), multiplier=10) as loop:
                node = calc(x=PARENT["x"], multiplier=PARENT["multiplier"])
                START >> node >> END
        """
        inputs, init_kwargs = split_iter_kwargs(kwargs)
        return cls(inputs=inputs, **init_kwargs)

    def serialize(self) -> dict:
        """Serialize ForOp for Rust backend."""
        base = super().serialize()
        base["fail_fast"] = self._fail_fast
        return base

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return subclass-specific metadata."""
        return {"each": list(self._each.keys()), "inputs": list(self._broadcast_inputs.keys())}
