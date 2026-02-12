"""Async iteration node for processing async streaming data."""

import asyncio
import os
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncIterable,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
)

from hush.core.configs.op_config import OpType
from hush.core.loggings import LOGGER
from hush.core.ops.base import shorthand
from hush.core.ops.iteration.base import BaseIterationOp, get_iter_context, split_iter_kwargs
from hush.core.states.ref import Ref
from hush.core.utils.common import Param

if TYPE_CHECKING:
    from hush.core.states import MemoryState


def batch_by_size(n: int) -> Callable[[List, Any], bool]:
    """Create batch condition to flush when batch reaches n items."""
    return lambda batch, _: len(batch) >= n


class AIterOp(BaseIterationOp):
    """Streaming node for async iterable data with optional batching.

    Features:
    - Concurrent processing with ordered output emission
    - Optional batching before processing
    - Optional callback for streaming results
    - Semaphore-limited concurrency

    Example:
        async def my_stream():
            for i in range(10):
                yield {"value": i}

        with AIterOp(
            inputs={
                "item": Each(my_stream()),    # async iterable source
                "multiplier": 10               # broadcast
            },
            callback=handle_result
        ) as stream_node:
            processor = process(inputs={"item": PARENT["item"]})
            START >> processor >> END
    """

    type: OpType = "stream"

    __slots__ = ["_max_concurrency", "_callback", "_batch_fn"]

    def __init__(
        self,
        inputs: Optional[Dict[str, Any]] = None,
        callback: Optional[Callable[[Any], Awaitable[None]]] = None,
        batch_fn: Optional[Callable[[List, Any], bool]] = None,
        max_concurrency: Optional[int] = None,
        **kwargs,
    ):
        """Initialize AIterOp.

        Args:
            inputs: Dict with exactly one Each(async_iterable) and optional broadcast values.
            callback: Optional async function called with each result in order.
            batch_fn: Optional function (batch, new_item) -> should_flush.
            max_concurrency: Max concurrent processing tasks. Defaults to CPU count.
        """
        super().__init__(inputs=inputs, **kwargs)

        # Validate exactly one Each() source
        if len(self._each) != 1:
            raise ValueError(
                f"AIterOp requires exactly one Each() source, got: {list(self._each.keys())}"
            )

        self._callback = callback
        self._batch_fn = batch_fn
        self._max_concurrency = max_concurrency if max_concurrency is not None else os.cpu_count()

    def _post_build(self):
        """Setup inputs/outputs after graph is built."""
        # Normalize each and broadcast inputs
        normalized_each = self._normalize_params(self._each)
        normalized_broadcast = self._normalize_params(self._broadcast_inputs)

        self._each = {k: v.value for k, v in normalized_each.items()}
        self._broadcast_inputs = {k: v.value for k, v in normalized_broadcast.items()}

        iter_var_name = list(self._each.keys())[0]

        # Build inputs
        parsed_inputs = {
            iter_var_name: Param(type=AsyncIterable, required=True, value=self._each[iter_var_name])
        }

        if self._batch_fn is not None:
            parsed_inputs["batch"] = Param(type=List, required=False)
            parsed_inputs["batch_size"] = Param(type=int, required=False)

        parsed_inputs.update(
            {
                var_name: Param(type=Any, required=isinstance(value, Ref), value=value)
                for var_name, value in self._broadcast_inputs.items()
            }
        )

        # Build outputs
        existing_outputs = self.outputs or {}
        graph_outputs = self.outputs or {}
        parsed_outputs = {
            key: Param(type=List, required=param.required) for key, param in graph_outputs.items()
        }
        parsed_outputs["iteration_metrics"] = Param(type=Dict, required=False)

        if self._raw_outputs is not None:
            self.outputs = self._merge_params(parsed_outputs, self._raw_outputs)
        else:
            self.outputs = parsed_outputs

        for key, existing_param in existing_outputs.items():
            if key in self.outputs and existing_param.value is not None:
                self.outputs[key].value = existing_param.value

        self.inputs = parsed_inputs

    def _get_source(self, state: "MemoryState", parent_context: Optional[str]) -> AsyncIterable:
        """Get async iterable source, resolving Ref if needed."""
        iter_var_name = list(self._each.keys())[0]
        source = self._each[iter_var_name]

        if isinstance(source, Ref):
            raw = state[source.source, source.var, parent_context]
            return source._fn(raw)
        return source

    async def _process_chunk(
        self,
        chunk_data: Dict[str, Any],
        chunk_id: int,
        state: "MemoryState",
        request_id: str,
        base_context: Optional[str],
    ) -> Dict[str, Any]:
        """Process single chunk through graph."""
        chunk_context = get_iter_context((base_context + ".") if base_context else "", chunk_id)

        try:
            for var_name, value in chunk_data.items():
                state[self.full_name, var_name, chunk_context] = value

            result = await self._run_graph(state, chunk_context, chunk_context)
            return {
                "chunk_id": chunk_id,
                "result": result,
                "success": True,
            }
        except Exception as e:
            LOGGER.error(
                "[title]\\[%s][/title] Error processing chunk [value]%s[/value]: %s",
                request_id,
                chunk_id,
                e,
            )
            return {
                "chunk_id": chunk_id,
                "result": None,
                "success": False,
                "error": str(e),
            }

    async def _execute(
        self,
        state: "MemoryState",
        context_id: Optional[str],
        parent_context: Optional[str],
        request_id: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Process streaming data with concurrent processing but ordered output."""
        source = self._get_source(state, parent_context)
        broadcast_values = self._resolve_values(self._broadcast_inputs, state, parent_context)
        iter_var_name = list(self._each.keys())[0]

        _inputs = {iter_var_name: "<AsyncIterable>", **broadcast_values}

        if source is None:
            LOGGER.warning(
                "[title]\\[%s][/title] AIterOp [highlight]%s[/highlight]: source is None.",
                request_id,
                self.full_name,
            )

        semaphore = asyncio.Semaphore(self._max_concurrency)
        result_queue: asyncio.Queue = asyncio.Queue()

        success_count = 0
        error_count = 0
        handler_error_count = 0
        total_chunks = 0
        collected_results: List[Dict[str, Any]] = []

        async def process_with_semaphore(chunk_data: Dict[str, Any], cid: int):
            async with semaphore:
                result = await self._process_chunk(chunk_data, cid, state, request_id, context_id)
                await result_queue.put((cid, result))

        async def consume_stream():
            nonlocal total_chunks
            current_batch: List = []
            tasks = []

            async for item in source:
                if self._batch_fn:
                    if current_batch and self._batch_fn(current_batch, item):
                        batch_data = broadcast_values.copy()
                        batch_data["batch"] = current_batch
                        batch_data["batch_size"] = len(current_batch)
                        tasks.append(
                            asyncio.create_task(process_with_semaphore(batch_data, total_chunks))
                        )
                        total_chunks += 1
                        current_batch = []
                    current_batch.append(item)
                else:
                    chunk_data = broadcast_values.copy()
                    chunk_data[iter_var_name] = item
                    tasks.append(
                        asyncio.create_task(process_with_semaphore(chunk_data, total_chunks))
                    )
                    total_chunks += 1

            if self._batch_fn and current_batch:
                batch_data = broadcast_values.copy()
                batch_data["batch"] = current_batch
                batch_data["batch_size"] = len(current_batch)
                tasks.append(asyncio.create_task(process_with_semaphore(batch_data, total_chunks)))
                total_chunks += 1

            if tasks:
                await asyncio.gather(*tasks)
            await result_queue.put(None)

        consumer_task = asyncio.create_task(consume_stream())

        pending_results: Dict[int, Dict] = {}
        next_id = 0

        while True:
            item = await result_queue.get()
            if item is None:
                break

            cid, result = item
            pending_results[cid] = result

            while next_id in pending_results:
                result = pending_results.pop(next_id)

                if result.get("success"):
                    success_count += 1

                    if self._callback:
                        try:
                            await self._callback(result["result"])
                        except Exception as e:
                            handler_error_count += 1
                            LOGGER.error("[title]\\[%s][/title] Callback error: %s", request_id, e)

                    collected_results.append(result["result"])
                else:
                    error_count += 1
                    collected_results.append({"error": result.get("error")})

                next_id += 1

        await consumer_task

        iteration_metrics = {
            "total_iterations": total_chunks,
            "success_count": success_count,
            "error_count": error_count,
        }

        if total_chunks > 0 and error_count / total_chunks > 0.1:
            LOGGER.warning(
                "[title]\\[%s][/title] AIterOp [highlight]%s[/highlight]: high error rate [muted](%s)[/muted].",
                request_id,
                self.full_name,
                f"{error_count / total_chunks:.1%}",
            )

        if self._callback:
            iteration_metrics["callback"] = {
                "error_count": handler_error_count,
            }

        output_keys = [k for k in self.outputs.keys() if k != "iteration_metrics"]
        _outputs = {key: [r.get(key) for r in collected_results] for key in output_keys}
        _outputs["iteration_metrics"] = iteration_metrics

        return _inputs, _outputs

    @shorthand
    def of(cls, **kwargs) -> "AIterOp":
        """Create an AIterOp with flat kwargs.

        Example::

            with AIterOp.of(item=Each(my_stream()), multiplier=10, callback=handle_result) as stream:
                processor = process(item=PARENT["item"], multiplier=PARENT["multiplier"])
                START >> processor >> END
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
            "has_callback": self._callback is not None,
            "has_batch_fn": self._batch_fn is not None,
        }
