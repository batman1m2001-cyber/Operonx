"""MapNode - parallel iteration node applying function to each item in a collection."""

import asyncio
import os
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from hush.core.configs.node_config import NodeType
from hush.core.exceptions import IterationError
from hush.core.loggings import LOGGER
from hush.core.nodes.base import shorthand
from hush.core.nodes.iteration.base import BaseIterationNode, get_iter_context, split_iter_kwargs

if TYPE_CHECKING:
    from hush.core.states import MemoryState


class MapNode(BaseIterationNode):
    """Parallel iteration node - applies function to each item concurrently.

    Use MapNode when:
        - Items are independent and can be processed in parallel
        - Order of execution doesn't matter (results are collected in order)
        - You want maximum throughput for I/O-bound operations

    Example:
        with MapNode(
            name="process_map",
            inputs={
                "x": Each([1, 2, 3]),           # iterate
                "multiplier": 10                 # broadcast
            },
            max_concurrency=4
        ) as map_node:
            node = calc(inputs={"x": PARENT["x"], "multiplier": PARENT["multiplier"]})
            START >> node >> END
    """

    type: NodeType = "map"

    __slots__ = ["_max_concurrency", "_fail_fast"]

    def __init__(
        self,
        inputs: Optional[Dict[str, Any]] = None,
        max_concurrency: Optional[int] = None,
        fail_fast: bool = False,
        **kwargs,
    ):
        """Initialize MapNode.

        Args:
            inputs: Dict mapping variable names to values or Each(source).
            max_concurrency: Max concurrent tasks. Defaults to CPU count.
            fail_fast: If True, raise IterationError on first failure instead of continuing.
        """
        super().__init__(inputs=inputs, **kwargs)
        self._max_concurrency = max_concurrency if max_concurrency is not None else os.cpu_count()
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
        """Execute map loop through iteration data in parallel."""
        each_values = self._resolve_values(self._each, state, parent_context)
        broadcast_values = self._resolve_values(self._broadcast_inputs, state, parent_context)

        iteration_data = self._build_iteration_data(each_values, broadcast_values)
        _inputs = {**each_values, **broadcast_values}

        if not iteration_data:
            LOGGER.warning(
                "[title]\\[%s][/title] MapNode [highlight]%s[/highlight]: no iteration data.",
                request_id,
                self.full_name,
            )

        semaphore = asyncio.Semaphore(self._max_concurrency)
        total_iterations = len(iteration_data)

        async def execute_iteration(
            i: int, iter_context: str, loop_data: Dict[str, Any]
        ) -> Dict[str, Any]:
            try:
                async with semaphore:
                    for var_name, value in loop_data.items():
                        state[self.full_name, var_name, iter_context] = value
                    result = await self._run_graph(state, iter_context, iter_context)
                return {"result": result, "success": True, "index": i}
            except Exception as e:
                error = IterationError(
                    message=f"Iteration {i} failed",
                    iteration_index=i,
                    loop_data=loop_data,
                    total_iterations=total_iterations,
                    node_type="map",
                    original_error=e,
                )
                if self._fail_fast:
                    raise error from e
                LOGGER.warning(str(error))
                return {
                    "result": {"error": str(e), "error_type": type(e).__name__},
                    "success": False,
                    "index": i,
                }

        ctx_prefix = (context_id + ".") if context_id else ""
        raw_results = await asyncio.gather(
            *[
                execute_iteration(i, get_iter_context(ctx_prefix, i), data)
                for i, data in enumerate(iteration_data)
            ]
        )

        # Extract metrics and results
        final_results = []
        success_count = 0
        for r in raw_results:
            final_results.append(r["result"])
            success_count += r["success"]
        error_count = len(raw_results) - success_count

        iteration_metrics = {
            "total_iterations": len(iteration_data),
            "success_count": success_count,
            "error_count": error_count,
        }

        if iteration_data and error_count / len(iteration_data) > 0.1:
            LOGGER.warning(
                "[title]\\[%s][/title] MapNode [highlight]%s[/highlight]: high error rate [muted](%s)[/muted].",
                request_id,
                self.full_name,
                f"{error_count / len(iteration_data):.1%}",
            )

        output_keys = [k for k in self.outputs.keys() if k != "iteration_metrics"]
        _outputs = {key: [r.get(key) for r in final_results] for key in output_keys}
        _outputs["iteration_metrics"] = iteration_metrics

        return _inputs, _outputs

    @shorthand
    def of(cls, **kwargs) -> "MapNode":
        """Create a MapNode with flat kwargs.

        Example::

            with MapNode.of(x=Each([1, 2, 3]), multiplier=10, max_concurrency=4) as loop:
                node = calc(x=PARENT["x"], multiplier=PARENT["multiplier"])
                START >> node >> END
        """
        inputs, init_kwargs = split_iter_kwargs(kwargs)
        return cls(inputs=inputs, **init_kwargs)

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return subclass-specific metadata."""
        return {
            "max_concurrency": self._max_concurrency,
            "each": list(self._each.keys()),
            "inputs": list(self._broadcast_inputs.keys()),
        }
