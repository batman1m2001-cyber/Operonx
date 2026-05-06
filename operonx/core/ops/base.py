"""BaseOp — base class for all ops in a workflow."""

import asyncio
import inspect
import traceback
import uuid
from abc import ABC
from datetime import datetime, timezone
from logging import ERROR, INFO, WARNING
from time import perf_counter
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable, Dict, List, Optional, Union

from operonx.core.loggings import LOGGER, format_event, format_log_data
from operonx.core.media import Media
from operonx.core.ops._events import Interrupt
from operonx.core.ops._params import merge_params, normalize_params, resolve_value
from operonx.core.tracing.emitter import _current_op_var, current_emitter
from operonx.core.states.cell import DEFAULT_CONTEXT
from operonx.core.states.ref import Ref
from operonx.core.states.scratch_ref import ScratchRef
from operonx.core.utils.auto_name import auto_name, unique_name
from operonx.core.utils.common import Param
from operonx.core.utils.context import get_current

if TYPE_CHECKING:
    from operonx.core.states import MemoryState


# Re-export shorthand utilities for backward compatibility
from operonx.core.ops._shortcuts import (  # noqa: F401, E402
    _BASE_INIT_KEYS,
    shorthand,
    split_shorthand_kwargs,
)
from operonx.core.ops._utils import _has_explicit_outputs, _set_wildcard_outputs  # noqa: F401, E402


def _unwrap_media_in_place(inputs: Dict[str, Any]) -> None:
    """Strip top-level ``Media`` wrappers so consumer ops receive raw values.

    The collector's raw state read preserves ``Media``; only input binding for
    op execution drops the wrapper. Nested ``Media`` (inside dicts / lists) is
    left alone — those are rare and usually intentional.
    """
    for k, v in inputs.items():
        if isinstance(v, Media):
            inputs[k] = v.data


class BaseOp(ABC):
    """Base class for all ops in a workflow.

    An op is the fundamental processing unit. Each op declares typed inputs
    and outputs (via ``Param``), and implements a ``core()`` method that
    contains the execution logic. Ops are wired together inside a ``GraphOp``
    using edge operators.

    Sections (read top-to-bottom)::

        1. INIT            __init__, __slots__, param helpers
        2. EDGE OPERATORS  >>, >>~, >, <, [], ~ — wiring ops in a graph
        3. EXECUTE         run(), get_inputs/outputs, store_result, _exec_core
        4. OBSERVABILITY   _log(), _store_metrics()
        5. SERIALIZATION   serialize(), metadata — for Rust backend & tracing

    Example::

        from operonx.core import GraphOp, op, START, END, PARENT

        @op
        def double(x: int):
            return {"result": x * 2}

        with GraphOp(name="main") as graph:
            d = double(x=PARENT["x"])
            START >> d >> END
    """

    INNER_PROCESS = "__inner__"

    __slots__ = [
        "id",
        "name",
        "_full_name",
        "description",
        "type",
        "stream",
        "start",
        "end",
        "verbose",
        "sources",
        "targets",
        "inputs",
        "outputs",
        "core",
        "parent",
        "contain_generation",
        "enabled",
        "bound",
        "cache",
        "delay",
        "is_gen",
        "_input_cache",
        "_metrics_idx",
        "_error_idx",
    ]

    # Class-level cache stores shared across instances: {op_full_name: (path_or_none, {hash: result})}
    _cache_stores: Dict[str, tuple] = {}

    _VALID_BOUNDS = (None, "sync", "io", "cpu")

    def __init__(
        self,
        id: str = None,
        name: str = None,
        description: str = "",
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        sources: List[str] = None,
        targets: List[str] = None,
        stream: bool = False,
        start: bool = False,
        end: bool = False,
        contain_generation: bool = False,
        verbose: bool = True,
        enabled: bool = True,
        executor: Optional[str] = None,
        bound: Optional[str] = None,
        cache: Union[bool, str, None] = None,
        delay: float = 0,
    ):
        # Backward compat: executor="thread" → bound="cpu"
        if executor == "thread" and bound is None:
            bound = "cpu"
        if bound not in self._VALID_BOUNDS:
            raise ValueError(
                f"bound must be 'sync', 'io', 'cpu', or None (auto-detect), got {bound!r}"
            )
        self.bound = bound  # resolved to concrete value by _set_core()
        self._input_cache = None  # built on first get_inputs() call
        self._metrics_idx = None  # (schema, st_idx, et_idx, dur_idx)
        self._error_idx = None  # (schema, err_idx)
        self.cache = cache
        self.delay = delay
        self.id = id or uuid.uuid4().hex
        if name is None:
            name = auto_name()
        self.name = name or unique_name()
        self._full_name = None  # Cached at build time by GraphOp.build()
        self.description = description

        self.stream = stream
        self.start = start
        self.end = end
        self.verbose = verbose
        self.enabled = enabled

        self.sources: List[str] = sources or []
        self.targets: List[str] = targets or []

        self.core: Optional[Callable] = None
        self.is_gen: bool = False
        self.contain_generation = contain_generation
        # Đăng ký vào graph cha
        self.parent = get_current()
        # Use getattr to avoid hasattr's double lookup
        add_op = getattr(self.parent, "add_op", None)
        if add_op is not None:
            add_op(self)

        # Validate op name
        if self.name and not self.name.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                f"Op name '{self.name}' may only contain alphanumeric characters, underscores, and hyphens"
            )

        # Normalize inputs/outputs to Dict[str, Param]
        self.inputs: Dict[str, Param] = self._normalize_params(inputs)
        self.outputs: Dict[str, Param] = self._normalize_params(outputs)

        # Error if there are overlapping keys between inputs and outputs
        if self.inputs and self.outputs:
            overlapping_keys = set(self.inputs.keys()) & set(self.outputs.keys())
            if overlapping_keys:
                raise ValueError(
                    f"Op '{self.name}' has overlapping input/output keys: {overlapping_keys}. "
                    "Input and output variable names must be different."
                )

    def _resolve_value(self, key: str, value: Any) -> Any:
        """Convert value to a Ref or keep as a literal."""
        return resolve_value(key, value, self.parent)

    def _normalize_params(self, params: Any) -> Dict[str, Param]:
        """Normalize inputs/outputs to Dict[str, Param]."""
        return normalize_params(params, self.parent)

    def _merge_params(
        self, schema: Dict[str, Param], user_provided: Dict[str, Any]
    ) -> Dict[str, Param]:
        """Merge schema (from parsing) with user-provided inputs/outputs."""
        return merge_params(schema, user_provided, self.parent)

    def _init_io(
        self,
        input_schema: Dict[str, Param],
        output_schema: Dict[str, Param],
        inputs: Any,
        outputs: Any,
    ) -> None:
        """Normalise and merge parsed I/O schema with user-provided mappings.

        Called by concrete op ``__init__`` implementations after
        ``super().__init__(**kwargs)`` to set ``self.inputs`` and
        ``self.outputs`` in one step.
        """
        self.inputs = self._merge_params(input_schema, self._normalize_params(inputs))
        self.outputs = self._merge_params(output_schema, self._normalize_params(outputs))

    # =========================================================================
    # 1. INIT — identity, params, parent registration
    # =========================================================================

    @property
    def full_name(self) -> str:
        """Fully-qualified hierarchical path of this op. Cached after build()."""
        if self._full_name is not None:
            return self._full_name
        if self.parent:
            return f"{self.parent.full_name}.{self.name}"
        return self.name

    def _cache_full_name(self) -> None:
        """Cache full_name as stored attribute. Called at build time."""
        self._full_name = self.full_name

    def __getitem__(self, item) -> "Ref":
        """Allow ``op["var"]`` syntax to reference an output as a Ref."""
        return Ref(self, item)

    # =========================================================================
    # 2. EDGE OPERATORS — wiring ops inside a GraphOp
    # =========================================================================

    def __invert__(self) -> "SoftEdge":
        """``~op``: mark this op for a soft-edge connection."""
        return SoftEdge(self)

    def __rshift__(self, other):
        """``op >> other``: connect this op to *other* with a hard edge."""
        edge_type = "condition" if self.type == "branch" else "normal"
        add_edge = getattr(self.parent, "add_edge", None)

        # Handle SoftEdge marker: a >> ~b
        if isinstance(other, SoftEdge):
            if add_edge is not None:
                add_edge(self.name, other.op.name, edge_type, soft=True)
            return other.op  # Return unwrapped op for chaining

        if isinstance(other, list):
            if add_edge is not None:
                for item in other:
                    # Check if item is SoftEdge
                    if isinstance(item, SoftEdge):
                        add_edge(self.name, item.op.name, edge_type, soft=True)
                    else:
                        add_edge(self.name, item.name, edge_type)
            return other
        elif getattr(other, "name", None) is not None:
            # Check if other is END - auto-set wildcard outputs
            if getattr(other, "name", None) == "__END__":
                _set_wildcard_outputs(self)
            if add_edge is not None:
                add_edge(self.name, other.name, edge_type)
            return other
        return NotImplemented

    def __rrshift__(self, other):
        """``[op1, op2] >> self``: connect a list of ops to this op."""
        edge_type = "condition" if self.type == "branch" else "normal"
        add_edge = getattr(self.parent, "add_edge", None)

        if isinstance(other, list):
            if add_edge is not None:
                for item in other:
                    add_edge(item.name, self.name, edge_type)
        return self

    def __gt__(self, other):
        """``op > other``: soft edge (does not count toward ready_count).

        Used after a branch — only one predecessor needs to complete.
        Note: Python's chained comparison means ``a > b > c`` is
        ``(a > b) and (b > c)``, so split into separate statements.
        """
        edge_type = "condition" if self.type == "branch" else "normal"
        add_edge = getattr(self.parent, "add_edge", None)

        if isinstance(other, list):
            if add_edge is not None:
                for item in other:
                    add_edge(self.name, item.name, edge_type, soft=True)
            return other
        elif getattr(other, "name", None) is not None:
            if add_edge is not None:
                add_edge(self.name, other.name, edge_type, soft=True)
            return other
        return NotImplemented

    def __lt__(self, other):
        """``op < other``: reverse soft edge.

        Also invoked by Python when ``[a, b] > self`` (list.__gt__
        returns NotImplemented, so Python falls back to self.__lt__).
        """
        edge_type = "condition" if self.type == "branch" else "normal"
        add_edge = getattr(self.parent, "add_edge", None)

        if isinstance(other, list):
            if add_edge is not None:
                for item in other:
                    add_edge(item.name, self.name, edge_type, soft=True)
            return self  # Return self to continue chaining: [a, b] > self > next
        elif getattr(other, "name", None) is not None:
            if add_edge is not None:
                add_edge(other.name, self.name, edge_type, soft=True)
            return self
        return NotImplemented

    # =========================================================================
    # 3. EXECUTE — run the op, read inputs, store outputs
    # =========================================================================

    def __call__(self, **kwargs) -> Dict[str, Any]:
        """Quick-test: call the op directly with keyword inputs."""
        from operonx.core.states import MemoryState, StateSchema

        # Auto-build unbuilt GraphOps
        if hasattr(self, "_is_building") and self._is_building and hasattr(self, "build"):
            self.build()

        state = MemoryState(StateSchema(op=self), inputs=kwargs)

        async def _collect():
            result = {}
            async for _, result in self.run(state):
                pass
            return result

        try:
            asyncio.get_running_loop()
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, _collect()).result()
        except RuntimeError:
            result = asyncio.run(_collect())
        return result

    def warmup(self) -> None:
        """Called by Operon engine after graph.build() when a ResourceHub is available.

        Override in provider ops (LLMOp, EmbeddingOp, etc.) to eagerly initialize
        backends and eliminate cold-start latency on the first user request.

        The default implementation is a no-op — subclasses opt in by overriding.
        """

    def get_inputs(self, state: "MemoryState", context_id: str) -> Dict[str, Any]:
        """Retrieve input values from state based on connection mappings.

        Uses cached cell indices to avoid per-call schema.get_index() lookups.
        Falls back to standard path on first call to build the cache.
        """
        # Fast path: use cached indices to skip schema.get_index() dict lookup.
        # Cache is keyed by schema id since indices differ per schema.
        cached = self._input_cache
        if cached is not None and cached[0] is state.schema:
            result = {}
            cells = state._cells
            pull_refs = state.schema._pull_refs
            for var_name, idx, fallback in cached[1]:
                # Inline the hot path of state.__getitem__ without _unpack_key + get_index
                cell = cells[idx]
                if cell.is_shared or context_id in cell:
                    value = cell[context_id]
                else:
                    pull_ref = pull_refs[idx]
                    if pull_ref and not pull_ref.is_output and pull_ref.idx >= 0:
                        source_val = cells[pull_ref.idx][context_id]
                        if source_val is not None or cells[pull_ref.idx].default_value is not None:
                            value = pull_ref._fn(source_val)
                            cell[context_id] = value
                        else:
                            value = cell.default_value
                    else:
                        value = cell[context_id]  # hierarchy walk
                if value is not None:
                    result[var_name] = value
                elif fallback is not None:
                    result[var_name] = fallback
            for var_name in list(result):
                val = result[var_name]
                if isinstance(val, ScratchRef):
                    result[var_name] = state._scratch.get(val.key)
            return result

        # First call (or schema changed): build index cache
        entries = []
        result = {}
        full_name = self.full_name
        for var_name, param in self.inputs.items():
            idx = state.schema.get_index(full_name, var_name)
            fallback = None
            if param.value is not None and not isinstance(param.value, Ref):
                fallback = param.value
            elif param.default is not None:
                fallback = param.default
            entries.append((var_name, idx, fallback))

            value = state[full_name, var_name, context_id]
            if value is not None:
                result[var_name] = value
            elif fallback is not None:
                result[var_name] = fallback
        self._input_cache = (state.schema, entries)
        for var_name in list(result):
            val = result[var_name]
            if isinstance(val, ScratchRef):
                result[var_name] = state._scratch.get(val.key)
        _unwrap_media_in_place(result)
        return result

    def get_outputs(self, state: "MemoryState", context_id: str) -> Dict[str, Any]:
        """Read output values from state.

        Reads directly from this op's output variables. Output connections
        (outputs={...}) are resolved by the schema at build time —
        they create refs at the destination, not at this op.

        Args:
            state: Workflow state.
            context_id: Context of this op.
        """
        return {var: state[self.full_name, var, context_id] for var in self.outputs}

    def normalize_trace_io(self, inputs: Dict[str, Any], outputs: Dict[str, Any]) -> tuple:
        """Produce a trace-time view of this op's I/O.

        Called by ``_extract_trace_io`` before media extraction. Subclasses
        override when their I/O carries media in a non-``Media`` shape (e.g.
        LLMOp wraps OpenAI chat-format ``image_url`` blocks into ``Media``
        instances). The real state value is untouched — this returns copies
        used only for trace capture.

        Default is identity: most ops never override.
        """
        return inputs, outputs

    def _extract_trace_io(self, side_dict: Dict[str, Any], *, root: str) -> tuple:
        """Compute trace-time view of one I/O side: normalize + extract media.

        Returns ``(stripped, media_refs)`` where ``stripped`` is a copy of
        ``side_dict`` with each ``Media`` instance replaced by a
        ``"<media:N>"`` placeholder, and ``media_refs`` is the parallel
        ``list[MediaRef]`` carrying blobs + field paths. Empty list when no
        Media was found.

        ``root`` must be ``"inputs"`` or ``"outputs"`` — used as the field
        path prefix so the exporter knows which side to substitute into.
        """
        from operonx.core.media import extract_media

        if not side_dict:
            return side_dict, []
        if root == "inputs":
            normed, _ = self.normalize_trace_io(side_dict, {})
        else:
            _, normed = self.normalize_trace_io({}, side_dict)
        return extract_media(normed, root)

    def store_result(self, state: "MemoryState", result: Dict[str, Any], context_id: str) -> None:
        """Store result dict into state.

        Uses state[op, var, ctx] = value for O(1) index-based storage.
        Extracts $tags special key for dynamic tagging.
        """
        if not result:
            return

        # Extract $tags before storing (don't store as output variable)
        tags = result.pop("$tags", None)
        if tags:
            state.add_tags(tags)

        for key, value in result.items():
            state[self.full_name, key, context_id] = value

    # =========================================================================
    # 3b. OP-LEVEL CACHE
    # =========================================================================

    def _get_cache_store(self) -> Dict[int, Dict]:
        """Get or create the in-memory cache store for this op."""
        key = self.full_name
        if key not in BaseOp._cache_stores:
            store: Dict[int, Dict] = {}
            path = self.cache if isinstance(self.cache, str) else None
            if path:
                store = self._load_cache_file(path)
            BaseOp._cache_stores[key] = (path, store)
        return BaseOp._cache_stores[key][1]

    @staticmethod
    def _cache_hash(inputs: Dict[str, Any]) -> int:
        """FNV-1a hash of JSON-serialized inputs."""
        import json

        data = json.dumps(inputs, sort_keys=True, default=str).encode()
        h = 0xCBF29CE484222325
        for b in data:
            h ^= b
            h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        return h

    @staticmethod
    def _load_cache_file(path: str) -> Dict[int, Dict]:
        """Load cache from binary file."""
        import json
        import struct
        from pathlib import Path

        store: Dict[int, Dict] = {}
        p = Path(path)
        if not p.exists():
            return store

        data = p.read_bytes()
        if len(data) < 8:
            return store

        (count,) = struct.unpack_from("<Q", data, 0)
        offset = 8
        for _ in range(count):
            if offset + 12 > len(data):
                break
            h, json_len = struct.unpack_from("<QI", data, offset)
            offset += 12
            if offset + json_len > len(data):
                break
            try:
                value = json.loads(data[offset : offset + json_len])
                store[h] = value
            except Exception:
                pass
            offset += json_len

        return store

    @staticmethod
    def save_all_caches() -> int:
        """Save all file-backed caches. Returns total entries saved."""
        import json
        import struct
        from pathlib import Path

        total = 0
        for _, (path, store) in BaseOp._cache_stores.items():
            if not path or not store:
                continue
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)

            buf = struct.pack("<Q", len(store))
            for h, value in store.items():
                json_bytes = json.dumps(value, default=str).encode()
                buf += struct.pack("<QI", h, len(json_bytes))
                buf += json_bytes

            p.write_bytes(buf)
            total += len(store)

        return total

    # =========================================================================
    # 4. OBSERVABILITY — logging and metrics
    # =========================================================================

    def _log(
        self,
        request_id: str,
        context_id: Optional[str],
        inputs: Dict[str, Any],
        outputs: Dict[str, Any],
        duration_ms: float,
    ) -> None:
        """Log execution summary with inputs, outputs, and duration."""
        # Check both verbose flag and logger level before formatting
        if self.verbose and LOGGER.isEnabledFor(INFO):
            msg = format_event(
                "op_done",
                request_id=request_id or "unknown",
                op_type=self.type.upper() if isinstance(self.type, str) else str(self.type).upper(),
                full_name=self.full_name,
                context=".".join(context_id)
                if isinstance(context_id, tuple)
                else (context_id or "main"),
                duration_ms=f"{duration_ms:.1f}",
                inputs=format_log_data(inputs),
                outputs=format_log_data(outputs),
            )
            LOGGER.info(msg)

    def _store_metrics(
        self,
        state: "MemoryState",
        context_id: Optional[str],
        *,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        duration_ms: float = 0,
    ) -> None:
        """Store execution metrics (timing) to state via cached indices."""
        cached = self._metrics_idx
        if cached is not None and cached[0] is state.schema:
            _, st_idx, et_idx, dur_idx = cached
        else:
            fn = self.full_name
            st_idx = state.schema.get_index(fn, "start_time")
            et_idx = state.schema.get_index(fn, "end_time")
            dur_idx = state.schema.get_index(fn, "duration_ms")
            self._metrics_idx = (state.schema, st_idx, et_idx, dur_idx)

        ctx = context_id if context_id is not None else DEFAULT_CONTEXT
        cells = state._cells
        if st_idx >= 0:
            cells[st_idx][ctx] = start_time
        if et_idx >= 0:
            cells[et_idx][ctx] = end_time
        if dur_idx >= 0:
            cells[dur_idx][ctx] = duration_ms

    def _set_core(self, fn: Callable) -> None:
        """Assign core function and resolve bound to a concrete value.

        bound resolution (when None):
          - async coroutine / async generator → "io"
          - sync function / sync generator   → "sync"
        """
        self.core = fn
        self.is_gen = inspect.isasyncgenfunction(fn) or inspect.isgeneratorfunction(fn)
        if self.bound is None and fn is not None:
            is_async = inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)
            self.bound = "io" if is_async else "sync"

    async def _exec_core(self, inputs: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """Unified dispatch — yields one result for normal ops, N for generators.

        Dispatch is based on ``self.bound`` (resolved by ``_set_core``):
          - ``"sync"``: direct call, no await (fastest path)
          - ``"io"``:   await coroutine / async generator
          - ``"cpu"``:  ``asyncio.to_thread()`` for sync, await for async
        """
        core_fn = self.core
        if self.bound == "sync":
            if self.is_gen:
                for result in core_fn(**inputs):
                    yield result
            else:
                yield core_fn(**inputs)
        elif self.bound == "cpu" and not self.is_gen and not inspect.iscoroutinefunction(core_fn):
            yield await asyncio.to_thread(core_fn, **inputs)
        elif self.is_gen:
            async for result in core_fn(**inputs):
                yield result
        else:
            yield await core_fn(**inputs)

    async def run(
        self,
        state: "MemoryState",
        context_id: Optional[str] = None,
    ) -> "AsyncGenerator[tuple[Optional[str], Dict[str, Any]], None]":
        """Execute this op as a uniform async generator.

        Every op — whether it uses ``return`` or ``yield`` — is driven through
        the same three-layer model:

        1. **User function** (``@op``): plain ``return {"k": v}`` or ``yield {"k": v}``.
           No awareness of Frame/EOF.
        2. **``BaseOp.run()``** (this method): wraps the user function via
           ``_exec_core()`` into a uniform async generator that yields
           ``(context_id, result)`` tuples.  Normal op → one yield.
           Generator op → N yields, each in its own stream context ``[i]``.
        3. **``Scheduler._pump()``**: consumes this generator.  Each yield becomes
           a ``Frame`` event on the queue; when the generator exhausts naturally,
           ``_pump`` emits one ``EOF`` event.  The user never writes Frame or EOF.

        Yields:
            ``(context_id, result)`` — one tuple per item produced by the op.
        """
        if not self.enabled:
            return

        _tracing = state.tracing
        start_time = None
        end_time = None
        perf_start = perf_counter()
        duration_ms = 0.0
        _inputs = {}
        _outputs = {}
        error_msg = None

        # New event-stream tracing: emit hooks alongside legacy state-cell writes.
        # Lookup is one ContextVar.get — cached as local to keep emit O(1).
        # NullEmitter when no pipeline bound, so calls cost ~one method dispatch.
        emitter = current_emitter()
        op_var_token = None
        idx = 0
        op_started = False
        op_cancelled = False
        ctx_for_end = context_id if context_id is not None else DEFAULT_CONTEXT

        try:
            if self.delay > 0:
                await asyncio.sleep(self.delay)

            _inputs = self.get_inputs(state, context_id)

            # Record timing AFTER inputs resolved — measures actual processing,
            # not time spent waiting for upstream ops in the scheduler queue.
            start_time = datetime.now(timezone.utc) if _tracing else None
            perf_start = perf_counter()

            # New tracing: OP_START fires after inputs resolve, before exec.
            # Tracks start_time on the emitter for cancel-emit duration calc (Rule 3).
            # Media extraction: walk inputs once, replace Media instances with
            # placeholder strings + accumulate refs for the exporter to upload.
            _trace_inputs, _input_media = self._extract_trace_io(_inputs, root="inputs")
            emitter.op_start(
                self.full_name,
                ctx_for_end,
                _trace_inputs,
                media_refs=_input_media,
            )
            op_started = True
            op_var_token = _current_op_var.set((self.full_name, ctx_for_end))

            # Cache check
            if self.cache is not None:
                _cache_key = self._cache_hash(_inputs)
                _cache_store = self._get_cache_store()
                if _cache_key in _cache_store:
                    _outputs = _cache_store[_cache_key]
                    self.store_result(state, _outputs, context_id)
                    yield context_id, _outputs
                    return

            base_ctx = context_id if context_id is not None else DEFAULT_CONTEXT
            _yield_start = perf_counter()
            async for result in self._exec_core(_inputs):
                ctx = base_ctx + (f"[{idx}]",) if self.is_gen else context_id
                # Interrupt is a scheduler control event, not a result dict.
                # Forward it untouched — _pump puts it on the queue as-is.
                if isinstance(result, Interrupt):
                    yield ctx, result
                    idx += 1
                    _yield_start = perf_counter()
                    continue
                self.store_result(state, result, ctx)
                _outputs = result
                if self.is_gen and _tracing:
                    _yield_end = perf_counter()
                    _now = datetime.now(timezone.utc)
                    self._store_metrics(
                        state,
                        ctx,
                        start_time=_now,
                        end_time=_now,
                        duration_ms=(_yield_end - _yield_start) * 1000,
                    )
                elif not self.is_gen and _tracing:
                    # Batch ops: record timing BEFORE yield. The yield
                    # suspends this generator until the scheduler resumes
                    # it, inflating duration if measured in finally.
                    _batch_end = perf_counter()
                    end_time = datetime.now(timezone.utc)
                    duration_ms = (_batch_end - perf_start) * 1000
                if not self.is_gen and self.cache is not None:
                    _cache_store[_cache_key] = result
                # New tracing: per-yield event for generator ops only.
                # Threshold (`@op(emit_yields=N)`) is a T2 task — for now N=1.
                # Strip Media off the yielded dict so raw bytes don't sit in
                # the buffer; the parent observation's outputs (last yielded)
                # gets media uploaded via the OP_END path instead.
                if self.is_gen:
                    _trace_yielded, _yield_media = self._extract_trace_io(
                        result,
                        root="outputs",
                    )
                    emitter.op_yield(
                        self.full_name,
                        ctx,
                        _trace_yielded,
                        idx,
                        media_refs=_yield_media,
                    )
                yield ctx, result
                idx += 1
                _yield_start = perf_counter()

        except asyncio.CancelledError:
            # Mark for the OP_END emit below. finally still runs because Python
            # guarantees it on CancelledError. Re-raise so the scheduler sees
            # cancellation propagate through normally (Phase B Interrupt sweep).
            op_cancelled = True
            raise
        except Exception:
            import sys

            error_msg = (
                traceback.format_exc()
                if LOGGER.isEnabledFor(ERROR)
                else f"{type(sys.exc_info()[1]).__name__}: {sys.exc_info()[1]}"
            )
            LOGGER.error(
                format_event(
                    "op_error",
                    request_id=state.request_id or "unknown",
                    name=self.name,
                    error=error_msg.rstrip(),
                ),
            )

        finally:
            if self.is_gen or not _tracing:
                # Generators: measure total wall-clock time across all yields.
                # Non-tracing: just compute duration for metrics.
                duration_ms = (perf_counter() - perf_start) * 1000
                end_time = datetime.now(timezone.utc) if _tracing else None
            # else: batch ops already set end_time/duration_ms before yield

            self._store_metrics(
                state,
                context_id,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
            )

            # Write error state via cached index
            err_cached = self._error_idx
            if err_cached is not None and err_cached[0] is state.schema:
                err_idx = err_cached[1]
            else:
                err_idx = state.schema.get_index(self.full_name, "error")
                self._error_idx = (state.schema, err_idx)
            if err_idx >= 0:
                ctx_key = context_id if context_id is not None else DEFAULT_CONTEXT
                if error_msg is not None:
                    state._cells[err_idx][ctx_key] = error_msg
                elif "error" not in _outputs:
                    state._cells[err_idx][ctx_key] = None

            if _tracing:
                self._log(state.request_id, context_id, _inputs, _outputs, duration_ms)

            # New tracing: OP_END fires regardless of legacy `_tracing` flag
            # — the emitter is NullEmitter when no pipeline is bound, so this
            # is free in the no-tracer path. Status reflects what happened.
            #
            # Cancel-before-start case (op_started=False): the body never ran,
            # no OP_START was emitted, so we skip OP_END too — no orphan event.
            if op_started:
                if op_cancelled:
                    status = "cancelled"
                elif error_msg is not None:
                    status = "error"
                else:
                    status = "ok"
                if status == "ok":
                    _trace_outputs, _output_media = self._extract_trace_io(
                        _outputs,
                        root="outputs",
                    )
                else:
                    _trace_outputs, _output_media = {}, []
                emitter.op_end(
                    self.full_name,
                    ctx_for_end,
                    outputs=_trace_outputs,
                    status=status,
                    duration_ms=duration_ms,
                    yield_count=idx,
                    media_refs=_output_media,
                )
            if op_var_token is not None:
                _current_op_var.reset(op_var_token)

            if duration_ms > 100 and LOGGER.isEnabledFor(WARNING):
                LOGGER.warning(
                    format_event(
                        "op_slow",
                        request_id=state.request_id or "unknown",
                        full_name=self.full_name,
                        duration_ms=f"{duration_ms:.1f}",
                    ),
                )

    # =========================================================================
    # 5. SERIALIZATION — for Rust backend and tracing
    # =========================================================================

    def serialize(self) -> dict:
        """Serialize this op to a config dict for the Rust backend."""
        is_async = inspect.iscoroutinefunction(self.core)
        is_gen = inspect.isgeneratorfunction(self.core) or inspect.isasyncgenfunction(self.core)
        base = {
            "type": self.type,
            "full_name": self.full_name,
            "name": self.name,
            "func_name": getattr(self.core, "_func_name", getattr(self.core, "__name__", None)),
            "python_callable": self.core,
            "is_async": is_async,
            "is_generator": is_gen,
            "enabled": self.enabled,
            "verbose": self.verbose,
            "stream": self.stream,
            "bound": self.bound,
            "inputs": self._serialize_params(self.inputs),
            "outputs": self._serialize_params(self.outputs),
        }
        if self.cache is not None:
            base["cache"] = self.cache
        if self.delay > 0:
            base["delay"] = self.delay
        return base

    def _serialize_params(self, params: Dict[str, "Param"]) -> dict:
        """Serialize a dict of Params."""
        if not params:
            return {}
        result = {}
        for name, param in params.items():
            entry = {
                "default": param.default,
                "required": param.required,
                "ref": None,
                "scratch": None,
                "literal": None,
            }
            if isinstance(param.value, Ref):
                entry["ref"] = param.value.serialize()
            elif isinstance(param.value, ScratchRef):
                entry["scratch"] = {"key": param.value.key}
            elif param.value is not None:
                entry["literal"] = param.value
            result[name] = entry
        return result

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return subclass-specific metadata. Override in subclasses."""
        return {}

    @property
    def metadata(self) -> Dict[str, Any]:
        """Build a metadata dictionary for this op."""

        def get_connect_key(param: Param):
            if isinstance(param.value, Ref):
                return {param.value.source: param.value.var}
            if isinstance(param.value, ScratchRef):
                return {"$scratch": param.value.key}
            return param.value

        result = {
            "id": self.id,
            "name": self.full_name,
            "type": self.type,  # Already a lowercase string (Literal type)
        }

        if self.description:
            result["description"] = self.description

        if self.inputs:
            result["input_connects"] = {k: get_connect_key(v) for k, v in self.inputs.items()}

        if self.outputs:
            result["output_connects"] = {k: get_connect_key(v) for k, v in self.outputs.items()}

        if self.sources:
            result["sources"] = f"<- ({','.join(self.sources)})"

        if self.targets:
            result["targets"] = f"-> ({','.join(self.targets)})"

        for flag in ["stream", "start", "end"]:
            if getattr(self, flag, False):
                result[flag] = True

        result.update({k: v for k, v in self.specific_metadata.items() if v})

        return result


# Re-export edge classes and singletons for backward compatibility
from operonx.core.ops._edges import (  # noqa: E402, F401
    END,
    PARENT,
    SCRATCH,
    START,
    DummyOp,
    ScratchAccessor,
    SoftEdge,
)
