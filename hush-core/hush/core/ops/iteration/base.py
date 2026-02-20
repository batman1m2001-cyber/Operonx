"""Base class for iteration ops with common infrastructure."""

import asyncio
import traceback
from datetime import datetime
from time import perf_counter
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from hush.core.loggings import LOGGER
from hush.core.ops.base import split_shorthand_kwargs
from hush.core.ops.graph.graph_op import GraphOp
from hush.core.states.ref import Ref
from hush.core.utils.common import Param

if TYPE_CHECKING:
    from hush.core.states import MemoryState


# Pre-computed context ID suffixes for indices 0-999
_CTX_SUFFIXES = tuple("[" + str(i) + "]" for i in range(1000))


def get_iter_context(prefix: str, i: int) -> str:
    """Get iteration context ID. Uses pre-computed strings for i < 1000."""
    if i < 1000:
        return prefix + _CTX_SUFFIXES[i]
    return prefix + "[" + str(i) + "]"


class Each:
    """Marker that designates an input as the iteration source.

    Inputs wrapped in ``Each()`` are iterated — each iteration receives one
    element. Unwrapped inputs are broadcast (same value every iteration).

    Example::

        with ForOp(inputs={
            "item": Each(items_op["items"]),      # iterated
            "multiplier": PARENT["multiplier"],   # broadcast
        }) as loop:
            ...
    """

    __slots__ = ["source"]

    def __init__(self, source: Any):
        self.source = source

    def __repr__(self) -> str:
        return f"Each({self.source!r})"


# Iteration-specific init keys (beyond base keys)
_ITER_EXTRA_KEYS = {
    "max_concurrency",
    "until",
    "max_iterations",
    "callback",
    "batch_fn",
    "fail_fast",
}


def split_iter_kwargs(kwargs: dict) -> tuple:
    """Split flat kwargs for iteration ops.

    Wrapper around split_shorthand_kwargs that:
    - Adds iteration-specific init keys
    - Validates Each() doesn't conflict with reserved keys
    """
    inputs, init_kwargs = split_shorthand_kwargs(kwargs, _ITER_EXTRA_KEYS)

    # Validate Each() values don't use reserved keys
    for key, value in inputs.items():
        if isinstance(value, Each) and key in init_kwargs:
            raise TypeError(
                f"'{key}' conflicts with a reserved keyword. "
                f"Please choose a different variable name."
            )

    return inputs, init_kwargs


class BaseIterationOp(GraphOp):
    """Base class for iteration ops (ForOp, MapOp, WhileOp, AIterOp).

    Provides shared infrastructure: child-graph execution, Ref resolution,
    iteration-data construction, and input/output normalisation. Subclasses
    only need to implement ``run()`` with their specific scheduling strategy
    (sequential, parallel, conditional, or streaming).

    Performance: ``_var_indices`` pre-computes state indices to avoid repeated
    lookups in hot loops.
    """

    __slots__ = ["_each", "_broadcast_inputs", "_raw_inputs", "_raw_outputs", "_var_indices"]

    def __init__(self, inputs: Optional[Dict[str, Any]] = None, **kwargs):
        """Initialize BaseIterationOp.

        Args:
            inputs: Dict mapping variable names to values or Each(source).
        """
        self._raw_outputs = kwargs.get("outputs")
        self._raw_inputs = inputs or {}

        super().__init__(**kwargs)

        # Separate Each() sources from broadcast inputs
        self._each: Dict[str, Any] = {}
        self._broadcast_inputs: Dict[str, Any] = {}

        for var_name, value in self._raw_inputs.items():
            if isinstance(value, Each):
                self._each[var_name] = value.source
            else:
                self._broadcast_inputs[var_name] = value

    def _post_build(self):
        """Setup inputs/outputs after graph is built. Override in subclasses."""
        pass

    async def _run_graph(
        self, state: "MemoryState", context_id: str, parent_context: str
    ) -> Dict[str, Any]:
        """Run child nodes with parent_context.

        Uses Rust scheduler when available, otherwise falls back to Python.
        """
        if self._compiled_graph_rs is not None:
            return await self._run_graph_rust(state, context_id, parent_context)
        return await self._run_graph_python(state, context_id, parent_context)

    async def _run_graph_rust(
        self, state: "MemoryState", context_id: str, parent_context: str
    ) -> Dict[str, Any]:
        """Run child nodes using CompiledGraph.

        Phase 5: Try inline Rust execution for Rust-native ops.
        """
        sched = self._compiled_graph_rs
        nodes = self._ops

        # Detect RustMemoryState
        try:
            from hush_runtime import RustMemoryState

            _rust_state = isinstance(state, RustMemoryState)
        except ImportError:
            _rust_state = False

        # Fast path: all-sync-Rust graph
        if _rust_state and sched.all_rust_executable():
            sched.run_sync(state, context_id, parent_context)
            return self.get_outputs(state, context_id, parent_context)

        # Hybrid path
        run_state = sched.new_run_state()
        active_tasks: Dict[str, asyncio.Task] = {}

        def _activate(op_name: str):
            if _rust_state:
                return sched.activate_successors_with_state(
                    op_name, run_state, state, context_id
                )
            branch_target = None
            if sched.is_branch_op(op_name):
                from hush.core.ops.base import END

                branch_target = nodes[op_name].get_target(state, context_id)
                if branch_target == END.name:
                    return ([], True)
            return (sched.activate_successors(op_name, run_state, branch_target), False)

        async def _schedule_ops(entries: list):
            queue = list(entries)
            while queue:
                name, can_inline = queue.pop(0)
                if can_inline and _rust_state:
                    ok = sched.try_execute_inline(name, state, context_id, parent_context)
                    if ok:
                        newly_ready, is_end = _activate(name)
                        if not is_end:
                            queue.extend(newly_ready)
                        continue
                op_obj = nodes[name]
                if can_inline:
                    await op_obj.run(state, context_id, parent_context)
                    newly_ready, is_end = _activate(name)
                    if not is_end:
                        queue.extend(newly_ready)
                else:
                    active_tasks[name] = asyncio.create_task(
                        name=name, coro=op_obj.run(state, context_id, parent_context)
                    )

        await _schedule_ops(sched.get_entries())

        while active_tasks:
            done_tasks, _ = await asyncio.wait(
                active_tasks.values(), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done_tasks:
                op_name = task.get_name()
                active_tasks.pop(op_name)
                newly_ready, is_end = _activate(op_name)
                if not is_end:
                    await _schedule_ops(newly_ready)

        return self.get_outputs(state, context_id, parent_context)

    async def _run_graph_python(
        self, state: "MemoryState", context_id: str, parent_context: str
    ) -> Dict[str, Any]:
        """Run child nodes using Python scheduler (original implementation)."""
        active_tasks: Dict[str, asyncio.Task] = {}
        ready_count = self.initial_ready_count.copy()
        soft_satisfied: set = set()

        for entry in self.entries:
            task = asyncio.create_task(
                name=entry, coro=self._ops[entry].run(state, context_id, parent_context)
            )
            active_tasks[entry] = task

        while active_tasks:
            done_tasks, _ = await asyncio.wait(
                active_tasks.values(), return_when=asyncio.FIRST_COMPLETED
            )

            nodes = self._ops
            nexts = self.nexts
            edges_lookup = self._edges

            for task in done_tasks:
                op_name = task.get_name()
                active_tasks.pop(op_name)
                current_op = nodes[op_name]

                if current_op.type == "branch":
                    branch_target = current_op.get_target(state, context_id)
                    from hush.core.ops.base import END

                    if branch_target != END.name:
                        next_nodes = [branch_target]
                    else:
                        next_nodes = []
                else:
                    next_nodes = nexts[op_name]

                for next_node in next_nodes:
                    edge = edges_lookup.get((op_name, next_node))
                    is_soft = edge and edge.soft

                    if is_soft:
                        if next_node in soft_satisfied:
                            continue
                        soft_satisfied.add(next_node)

                    ready_count[next_node] -= 1

                    if ready_count[next_node] == 0:
                        task = asyncio.create_task(
                            name=next_node,
                            coro=nodes[next_node].run(state, context_id, parent_context),
                        )
                        active_tasks[next_node] = task

        return self.get_outputs(state, context_id, parent_context)

    def _resolve_values(
        self, values: Dict[str, Any], state: "MemoryState", context_id: Optional[str]
    ) -> Dict[str, Any]:
        """Resolve values, dereference Ref objects."""
        result = {}
        for var_name, value in values.items():
            if isinstance(value, Ref):
                raw = state[value.source, value.var, context_id]
                result[var_name] = value._fn(raw, {})
            else:
                result[var_name] = value
        return result

    def _build_iteration_data(
        self, each_values: Dict[str, List], broadcast_values: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Build iteration data by zipping `each` values and adding broadcast."""
        if not each_values:
            return [broadcast_values.copy()] if broadcast_values else []

        lengths = {var: len(lst) for var, lst in each_values.items()}
        if len(set(lengths.values())) > 1:
            LOGGER.error(
                "%s [highlight]%s[/highlight]: 'each' variables have different lengths: %s",
                self.__class__.__name__,
                self.full_name,
                lengths,
            )
            raise ValueError(f"All 'each' variables must have the same length. Got: {lengths}")

        keys = list(each_values.keys())
        n = next(iter(lengths.values()))

        if broadcast_values:
            # dict.copy() is ~30% faster than {**dict} spread operator
            result = [broadcast_values.copy() for _ in range(n)]
            for i, vals in enumerate(zip(*each_values.values())):
                item = result[i]
                for j, k in enumerate(keys):
                    item[k] = vals[j]
        else:
            # Fast path: no broadcast values, build dicts directly with dict(zip())
            result = [dict(zip(keys, vals)) for vals in zip(*each_values.values())]

        return result

    def _normalize_iteration_io(self):
        """Common pattern for normalizing inputs/outputs in iteration nodes."""
        # Normalize each and broadcast inputs (resolve PARENT refs)
        normalized_each = self._normalize_params(self._each)
        normalized_broadcast = self._normalize_params(self._broadcast_inputs)

        self._each = {k: v.value for k, v in normalized_each.items()}
        self._broadcast_inputs = {k: v.value for k, v in normalized_broadcast.items()}

        # Build inputs
        parsed_inputs = {
            var_name: Param(type=List, required=isinstance(value, Ref), value=value)
            for var_name, value in self._each.items()
        }
        parsed_inputs.update(
            {
                var_name: Param(type=Any, required=isinstance(value, Ref), value=value)
                for var_name, value in self._broadcast_inputs.items()
            }
        )

        # Build outputs from graph outputs
        graph_outputs = self.outputs or {}
        parsed_outputs = {
            key: Param(type=List, required=param.required) for key, param in graph_outputs.items()
        }
        parsed_outputs["iteration_metrics"] = Param(type=Dict, required=False)

        # Preserve output mappings set via >> syntax
        existing_outputs = self.outputs or {}

        if self._raw_outputs is not None:
            self.outputs = self._merge_params(parsed_outputs, self._raw_outputs)
        else:
            self.outputs = parsed_outputs

        for key, existing_param in existing_outputs.items():
            if key in self.outputs and existing_param.value is not None:
                self.outputs[key].value = existing_param.value

        self.inputs = parsed_inputs

    async def run(
        self,
        state: "MemoryState",
        context_id: Optional[str] = None,
        parent_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Template method for iteration op execution.

        Handles common boilerplate:
        - State recording and timing
        - Exception handling and logging
        - Trace metadata recording

        Subclasses implement _execute() for specific iteration logic.
        """
        parent_name = self.father.full_name if self.father else None
        state.record_execution(self.full_name, parent_name, context_id)

        request_id = state.request_id
        start_time = datetime.now()
        perf_start = perf_counter()
        _inputs: Dict[str, Any] = {}
        _outputs: Dict[str, Any] = {}

        try:
            _inputs, _outputs = await self._execute(state, context_id, parent_context, request_id)
            self.store_result(state, _outputs, context_id)

        except Exception as e:
            error_msg = traceback.format_exc()
            LOGGER.error(
                "[title]\\[%s][/title] Error in op [highlight]%s[/highlight]: %s",
                request_id,
                self.full_name,
                str(e),
            )
            LOGGER.error(error_msg)
            state[self.full_name, "error", context_id] = error_msg

        finally:
            end_time = datetime.now()
            duration_ms = (perf_counter() - perf_start) * 1000
            self._log(request_id, context_id, _inputs, _outputs, duration_ms)
            state[self.full_name, "start_time", context_id] = start_time
            state[self.full_name, "end_time", context_id] = end_time
            state[self.full_name, "duration_ms", context_id] = duration_ms

        return _outputs

    async def _execute(
        self,
        state: "MemoryState",
        context_id: Optional[str],
        parent_context: Optional[str],
        request_id: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Execute iteration logic. Must be implemented by subclasses.

        Args:
            state: The memory state
            context_id: Current context ID
            parent_context: Parent context ID for resolving inputs
            request_id: Request ID for logging

        Returns:
            Tuple of (_inputs, _outputs) dicts
        """
        raise NotImplementedError("Subclasses must implement _execute()")
