"""BaseOp — base class for all ops in a workflow."""

import asyncio
import traceback
import uuid
from abc import ABC
from datetime import datetime
from time import perf_counter
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from hush.core.configs.op_config import OpType
from hush.core.loggings import LOGGER, format_log_data
from hush.core.states.ref import Ref
from hush.core.utils.auto_name import auto_name, register_skip, unique_name
from hush.core.utils.common import Param
from hush.core.utils.context import get_current

if TYPE_CHECKING:
    from hush.core.states import MemoryState


class SoftEdge:
    """Marker wrapper for a soft-edge connection.

    Use with ``~op`` syntax to mark a soft edge::

        a >> ~b       # soft edge from a to b
        a >> b        # hard edge from a to b
        [a, b] >> ~c  # soft edges from multiple ops
        ~a >> b       # soft edge, then continue chaining

    Soft edges do not count toward ready_count; used for branch outputs
    when only one branch executes.
    """

    __slots__ = ["op"]

    def __init__(self, op: "BaseOp"):
        self.op = op

    @property
    def name(self) -> str:
        return self.op.name

    def __rrshift__(self, other):
        """[a, b] >> ~self: soft edges from a list of ops to self.op."""
        add_edge = getattr(self.op.father, "add_edge", None)

        if isinstance(other, list):
            if add_edge is not None:
                for item in other:
                    edge_type = "condition" if getattr(item, "type", None) == "branch" else "normal"
                    add_edge(item.name, self.op.name, edge_type, soft=True)
            return self.op  # Return unwrapped for chaining
        elif getattr(other, "name", None) is not None:
            # single_op >> ~self
            edge_type = "condition" if getattr(other, "type", None) == "branch" else "normal"
            if add_edge is not None:
                add_edge(other.name, self.op.name, edge_type, soft=True)
            return self.op
        return NotImplemented

    def __rshift__(self, other):
        """~a >> b: after soft edge marker, continue with a hard edge.

        Allows chaining: source >> ~a >> b >> c
        After ~a is processed, continue the chain with b.
        """
        return self.op.__rshift__(other)


def _has_explicit_outputs(op) -> bool:
    """Check whether the op has user-defined explicit output mappings.

    Returns True if any output Param has a non-None value (user-set).
    Returns False if outputs are absent, empty, or all auto-parsed.
    """
    if not hasattr(op, "outputs") or op.outputs is None:
        return False
    if len(op.outputs) == 0:
        return False
    for param in op.outputs.values():
        if hasattr(param, "value") and param.value is not None:
            return True
    return False


# Base init keys shared by ALL nodes (from BaseOp.__init__)
_BASE_INIT_KEYS = frozenset(
    {
        "name",
        "id",
        "description",
        "inputs",
        "outputs",
        "sources",
        "targets",
        "stream",
        "start",
        "executor",
    }
)


def split_shorthand_kwargs(kwargs: dict, extra_init_keys: set = None) -> tuple:
    """Split flat kwargs into (inputs, init_kwargs).

    Used by shorthand functions (llm_, for_, op, etc.) to separate
    op constructor kwargs from input mappings.

    Args:
        kwargs: Flat keyword arguments from shorthand function.
        extra_init_keys: Additional op-specific init keys beyond base keys
            (e.g., {'max_concurrency', 'callback'} for iteration ops).

    Returns:
        (inputs, init_kwargs) tuple where:
        - inputs: Dict of input variable mappings
        - init_kwargs: Dict of op constructor arguments

    Example:
        # Provider ops - just base keys
        inputs, init_kwargs = split_shorthand_kwargs(kwargs)

        # Iteration ops - with extra keys
        inputs, init_kwargs = split_shorthand_kwargs(
            kwargs,
            {'max_concurrency', 'until', 'callback'}
        )
    """
    init_keys = _BASE_INIT_KEYS | (extra_init_keys or set())

    inputs = {}
    init_kwargs = {}

    for key, value in kwargs.items():
        if key in init_keys and not isinstance(value, Ref):
            init_kwargs[key] = value
        else:
            if key in init_keys:
                LOGGER.warning(
                    "Keyword '%s' is a reserved op parameter (one of %s) but received a Ref value. "
                    "It will be treated as an input mapping instead of an op constructor arg. "
                    "Consider renaming this parameter to avoid ambiguity.",
                    key,
                    sorted(init_keys),
                )
            inputs[key] = value

    return inputs, init_kwargs


def shorthand(fn):
    """Decorator for ``Op.of()`` classmethods.

    Registers the function for auto-naming frame skip via ``register_skip()``
    and wraps as ``classmethod``.

    Usage::

        class MyOp(BaseOp):
            @shorthand
            def of(cls, my_param=None, **kwargs):
                inputs, init_kwargs = split_shorthand_kwargs(kwargs)
                return cls(my_param=my_param, inputs=inputs or None, **init_kwargs)
    """
    register_skip(fn)
    return classmethod(fn)


class BaseOp(ABC):
    """Base class for all ops in a workflow.

    An op is the fundamental processing unit. Each op declares typed inputs
    and outputs (via ``Param``), and implements a ``core()`` method that
    contains the execution logic. Ops are wired together inside a ``GraphOp``
    using edge operators.

    Inputs:
        Defined per subclass via ``self.inputs: Dict[str, Param]``.

    Outputs:
        Defined per subclass via ``self.outputs: Dict[str, Param]``.

    Edge operators:
        - ``>>``  : Hard edge (sequential dependency, counts toward ready_count).
        - ``>>~`` : Soft edge (conditional, for branch merging — only one
          predecessor needs to complete).

    Example::

        from hush.core import GraphOp, op, START, END, PARENT

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
        "father",
        "contain_generation",
        "enabled",
        "executor",
    ]

    _VALID_EXECUTORS = (None, "thread")

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
    ):
        if executor not in self._VALID_EXECUTORS:
            raise ValueError(
                f"executor must be 'thread', 'process', or None, got {executor!r}"
            )
        self.executor = executor
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
        self.contain_generation = contain_generation
        # Đăng ký vào graph cha
        self.father = get_current()
        # Use getattr to avoid hasattr's double lookup
        add_op = getattr(self.father, "add_op", None)
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
        """Convert value to a Ref or keep as a literal.

        Supported formats:
            - some_op → Ref(some_op, key)
            - some_op["other"] → Ref(some_op, "other")
            - Ref(op, "var") → kept as-is
            - PARENT["x"] → Ref(father, "x")
            - literal → kept as-is
        """

        def resolve_parent(source):
            """Resolve PARENT marker to the actual father op."""
            if hasattr(source, "name") and source.name == "__PARENT__":
                return self.father if self.father else source
            return source

        # Handle Ref directly — keep operations intact
        if isinstance(value, Ref):
            resolved = resolve_parent(value.raw_source)
            return Ref(resolved, value.var, value.ops)

        # Handle op reference: some_op → Ref(some_op, key)
        if hasattr(value, "name"):
            resolved = resolve_parent(value)
            return Ref(resolved, key)

        # Literal value
        return value

    def _normalize_params(self, params: Any) -> Dict[str, Param]:
        """Normalize inputs/outputs to Dict[str, Param].

        Supported formats:
            - params=None → {}
            - params={"*": PARENT} → forward all keys from PARENT
            - params={"x": 1, "*": PARENT} → x=1, rest from PARENT
            - params={"var": Param(...)} → kept as-is, resolve value
            - params={"var": some_op} → {"var": Param(value=Ref(some_op, "var"))}
            - params={"var": some_op["other"]} → {"var": Param(value=Ref(some_op, "other"))}
            - params={"var": literal} → {"var": Param(value=literal)}
            - params={("a", "b"): some_op} → expanded to both keys
        """
        if params is None:
            return {}

        result = {}

        if isinstance(params, dict):
            for key, value in params.items():
                # Handle wildcard "*" key - store for later processing in _merge_params
                if key == "*":
                    # Validate that value is an op reference (PARENT or another op)
                    if hasattr(value, "name"):
                        result["__FORWARD_WILDCARD__"] = value
                    else:
                        raise ValueError(
                            f"Wildcard '*' key must have an op reference as value (e.g. PARENT), "
                            f"got: {type(value)}"
                        )
                    continue

                # Handle tuple keys: {("a", "b"): op} → expand to both
                if isinstance(key, tuple):
                    for k in key:
                        resolved_value = self._resolve_value(k, value)
                        result[k] = Param(value=resolved_value)
                # Handle Param directly
                elif isinstance(value, Param):
                    # Resolve value inside Param if present
                    if value.value is not None:
                        value.value = self._resolve_value(key, value.value)
                    result[key] = value
                else:
                    # Create new Param with resolved value (type auto-inferred)
                    resolved_value = self._resolve_value(key, value)
                    result[key] = Param(value=resolved_value)

        return result

    def _merge_params(
        self, schema: Dict[str, Param], user_provided: Dict[str, Any]
    ) -> Dict[str, Param]:
        """Merge schema (from parsing) with user-provided inputs/outputs.

        - If key already exists in schema → assign value only
        - If key is new → create new Param (type auto-inferred)
        - If user_provided has __FORWARD_WILDCARD__ marker → forward keys
          not explicitly defined to the op reference (PARENT)

        Args:
            schema: Dict[str, Param] from parsing (e.g. from function signature).
            user_provided: Dict from user (Ref | literal | Param).

        Returns:
            Merged Dict[str, Param].
        """
        # Copy schema to avoid mutating original
        result = {
            k: Param(
                type=v.type,
                required=v.required,
                default=v.default,
                description=v.description,
                value=v.value,
            )
            for k, v in schema.items()
        }

        # Resolve PARENT refs in schema values
        for key, param in result.items():
            if isinstance(param.value, Ref):
                param.value = self._resolve_value(key, param.value)

        if not user_provided:
            return result

        # Make a copy to avoid mutating the original dict
        user_provided = dict(user_provided)

        # Extract wildcard source if present
        wildcard_source = user_provided.pop("__FORWARD_WILDCARD__", None)

        # First, process explicitly provided keys
        explicitly_set = set()
        for key, value in user_provided.items():
            # Handle tuple keys
            if isinstance(key, tuple):
                for k in key:
                    self._merge_single_param(result, k, value)
                    explicitly_set.add(k)
            else:
                self._merge_single_param(result, key, value)
                explicitly_set.add(key)

        # Then, apply wildcard forwarding for remaining keys
        if wildcard_source is not None:
            # Resolve PARENT to father
            if hasattr(wildcard_source, "name") and wildcard_source.name == "__PARENT__":
                resolved_source = self.father if self.father else wildcard_source
            else:
                resolved_source = wildcard_source

            # Forward schema keys that weren't explicitly set
            for key in result:
                if key not in explicitly_set:
                    result[key].value = Ref(resolved_source, key)

        return result

    def _merge_single_param(self, result: Dict[str, Param], key: str, value: Any) -> None:
        """Merge a single param into the result dict."""
        if key in result:
            # Key already exists → assign value only
            if isinstance(value, Param):
                result[key].value = (
                    self._resolve_value(key, value.value) if value.value is not None else None
                )
            else:
                result[key].value = self._resolve_value(key, value)
        else:
            # New key → create new Param (type auto-inferred in Param.__post_init__)
            if isinstance(value, Param):
                if value.value is not None:
                    value.value = self._resolve_value(key, value.value)
                result[key] = value
            else:
                resolved_value = self._resolve_value(key, value)
                result[key] = Param(value=resolved_value)

    @property
    def full_name(self) -> str:
        """Fully-qualified hierarchical path of this op. Cached after build()."""
        if self._full_name is not None:
            return self._full_name
        if self.father:
            return f"{self.father.full_name}.{self.name}"
        return self.name

    def _cache_full_name(self) -> None:
        """Cache full_name as stored attribute. Called at build time."""
        self._full_name = self.full_name

    def identity(self, context_id: str) -> str:
        """Fully-qualified path with context_id."""
        return f"{self.full_name}[{context_id or 'main'}]"

    def __getitem__(self, item) -> "Ref":
        """Allow ``op["var"]`` syntax to reference an output as a Ref."""
        return Ref(self, item)

    def __invert__(self) -> "SoftEdge":
        """``~op``: mark this op for a soft-edge connection."""
        return SoftEdge(self)

    def __rshift__(self, other):
        """``op >> other``: connect this op to *other* with a hard edge."""
        edge_type = "condition" if self.type == "branch" else "normal"
        add_edge = getattr(self.father, "add_edge", None)

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
                if not _has_explicit_outputs(self):
                    if self.outputs is None:
                        self.outputs = {}
                    father = getattr(self, "father", None) or PARENT
                    for key in self.outputs:
                        param = self.outputs[key]
                        # Only update if param is a Param with value=None
                        if hasattr(param, "value") and param.value is None:
                            param.value = Ref(father, key)
            if add_edge is not None:
                add_edge(self.name, other.name, edge_type)
            return other
        return NotImplemented

    def __rrshift__(self, other):
        """``[op1, op2] >> self``: connect a list of ops to this op."""
        edge_type = "condition" if self.type == "branch" else "normal"
        add_edge = getattr(self.father, "add_edge", None)

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
        add_edge = getattr(self.father, "add_edge", None)

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
        add_edge = getattr(self.father, "add_edge", None)

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

    def __call__(self, **kwargs) -> Dict[str, Any]:
        """Quick-test: call the op directly with keyword inputs.

        Returns:
            Dict of outputs from executing the op.
        """
        from hush.core.states import MemoryState, StateSchema

        # Auto-build if this is an unbuilt GraphOp
        self._auto_build_if_needed()

        # Build schema from this op
        schema = StateSchema(op=self)
        # Pass kwargs into MemoryState to override inputs
        state = MemoryState(schema, inputs=kwargs)

        # Run synchronously
        try:
            loop = asyncio.get_running_loop()
            # If already in async context, run in thread pool
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, self.run(state)).result()
        except RuntimeError:
            # No running loop, use asyncio.run()
            result = asyncio.run(self.run(state))

        return result

    def _auto_build_if_needed(self):
        """Auto-build this op if it's a GraphOp that hasn't been built.

        This enables convenient testing: graph() will auto-build before execution.
        """
        # Check if this is a GraphOp (has _is_building attribute)
        if hasattr(self, "_is_building") and self._is_building:
            if hasattr(self, "build"):
                LOGGER.debug("Auto-building graph '%s' before execution", self.name)
                self.build()

    def is_base_op(self) -> bool:
        return True

    def get_inputs(
        self, state: "MemoryState", context_id: str, parent_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Retrieve input values from state based on connection mappings.

        Args:
            state: Workflow state.
            context_id: Context of this op.
            parent_context: Context used to resolve PARENT refs (for iteration ops).
        """
        result = {}

        for var_name, param in self.inputs.items():
            # Determine context based on ref target
            # PARENT ref → use parent_context (if available), sibling/other → use context_id
            if (
                parent_context is not None
                and isinstance(param.value, Ref)
                and param.value.raw_source is self.father
            ):
                lookup_ctx = parent_context
            else:
                lookup_ctx = context_id

            # Always read from state first (may have value from MemoryState inputs or Ref)
            value = state[self.full_name, var_name, lookup_ctx]

            if value is not None:
                result[var_name] = value
            elif param.value is not None and not isinstance(param.value, Ref):
                # Fallback: literal value in Param.value
                result[var_name] = param.value
            elif param.default is not None:
                # Fallback: default value
                result[var_name] = param.default

        return result

    def get_outputs(
        self, state: "MemoryState", context_id: str, parent_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Read output values from state.

        Reads directly from this op's output variables. Output connections
        (outputs={...}) are resolved by the schema at build time —
        they create refs at the destination, not at this op.

        Args:
            state: Workflow state.
            context_id: Context of this op.
            parent_context: Context of PARENT (for API consistency).
        """
        result = {}
        for var_name in self.outputs:
            result[var_name] = state[self.full_name, var_name, context_id]
        return result

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
        if self.verbose and LOGGER.isEnabledFor(20):  # 20 = INFO level
            LOGGER.info(
                "[title]\\[%s][/title] [bold]%s[/bold]: [highlight]%s[/highlight] [muted]\\[%s][/muted] [muted](%.1fms)[/muted] %s -> %s",
                request_id or "unknown",
                self.type.upper() if isinstance(self.type, str) else str(self.type).upper(),
                self.full_name,
                context_id or "main",
                duration_ms,
                format_log_data(inputs),
                format_log_data(outputs),
            )

    async def run(
        self,
        state: "MemoryState",
        context_id: Optional[str] = None,
        parent_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute this op.

        Args:
            state: Workflow state.
            context_id: Context for this op's own variables.
            parent_context: Context used to resolve PARENT refs.
        """
        parent_name = self.father.full_name if self.father else None
        state.record_execution(self.full_name, parent_name, context_id)

        # Skip disabled ops
        if not self.enabled:
            LOGGER.debug("Skipped disabled op: %s", self.full_name)
            return {}

        request_id = state.request_id
        start_time = datetime.now()
        perf_start = perf_counter()
        _inputs = {}
        _outputs = {}

        try:
            _inputs = self.get_inputs(state, context_id, parent_context)

            if asyncio.iscoroutinefunction(self.core):
                _outputs = await self.core(**_inputs)
            elif self.executor == "thread":
                _outputs = await asyncio.to_thread(self.core, **_inputs)
            else:
                _outputs = self.core(**_inputs)

            self.store_result(state, _outputs, context_id)

        except Exception:
            error_msg = traceback.format_exc()
            LOGGER.error(
                "[title]\\[%s][/title] Error in op [highlight]%s[/highlight]:\n%s",
                request_id,
                self.name,
                error_msg.rstrip(),
            )
            state[self.full_name, "error", context_id] = error_msg

        finally:
            end_time = datetime.now()
            duration_ms = (perf_counter() - perf_start) * 1000
            self._log(request_id, context_id, _inputs, _outputs, duration_ms)
            state[self.full_name, "start_time", context_id] = start_time
            state[self.full_name, "end_time", context_id] = end_time
            state[self.full_name, "duration_ms", context_id] = duration_ms

            # Performance monitoring: log slow ops (>100ms)
            if duration_ms > 100:
                LOGGER.warning(
                    "[title]\\[%s][/title] Slow op [highlight]%s[/highlight]: [red]%.1fms[/red]",
                    request_id,
                    self.full_name,
                    duration_ms,
                )

            return _outputs

    # =========================================================================
    # Serialization
    # =========================================================================

    def serialize(self) -> dict:
        """Serialize this op to a config dict for the Rust backend."""
        return {
            "type": self.type,
            "full_name": self.full_name,
            "name": self.name,
            "rust_op": getattr(self.core, "_rust_op_name", None),
            "python_callable": self.core,
            "is_async": asyncio.iscoroutinefunction(self.core),
            "executor": self.executor,
            "enabled": self.enabled,
            "verbose": self.verbose,
            "inputs": self._serialize_params(self.inputs),
            "outputs": self._serialize_params(self.outputs),
        }

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
                "literal": None,
            }
            if isinstance(param.value, Ref):
                entry["ref"] = param.value.serialize()
            elif param.value is not None:
                entry["literal"] = param.value
            result[name] = entry
        return result

    def get_input_variables(self) -> List[str]:
        """Trả về danh sách tên biến input."""
        return list(self.inputs.keys()) if self.inputs else []

    def get_output_variables(self) -> List[str]:
        """Trả về danh sách tên biến output."""
        return list(self.outputs.keys()) if self.outputs else []

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


class DummyOp(BaseOp):
    """Dummy op used as a marker for START, END, and PARENT."""

    type: OpType = "dummy"

    def __init__(self, name: str):
        super().__init__(name=name)

    def __rshift__(self, other):
        """START >> op or op >> END."""
        # Soft edges are not supported with START/END
        if isinstance(other, SoftEdge):
            raise TypeError(
                f"Cannot use soft edge (~) with {self.name}.\n"
                f"  Wrong: {self.name} >> ~op\n"
                f"  Right: {self.name} >> op"
            )

        if self == START:
            current_graph = get_current()
            if current_graph and hasattr(current_graph, "add_edge"):
                if isinstance(other, list):
                    for item in other:
                        current_graph.add_edge(self.name, item.name)
                    return other
                elif hasattr(other, "name"):
                    current_graph.add_edge(self.name, other.name)
                    return other
        return super().__rshift__(other)

    def __rrshift__(self, other):
        """[ops] >> START or [ops] >> END.

        When op >> END, auto-sets outputs = {"*": PARENT} if not already set.
        """
        current_graph = get_current()
        if current_graph and hasattr(current_graph, "add_edge"):
            if self == START:
                if isinstance(other, list):
                    for item in other:
                        current_graph.add_edge(self.name, item.name)
                elif hasattr(other, "name"):
                    current_graph.add_edge(self.name, other.name)
                return self

            elif self == END:
                # Auto-set outputs: each output key -> father[key]
                def _set_wildcard_outputs(target_op):
                    if hasattr(target_op, "outputs") and not _has_explicit_outputs(target_op):
                        if target_op.outputs is None:
                            target_op.outputs = {}
                        father = getattr(target_op, "father", None) or PARENT
                        for key in target_op.outputs:
                            param = target_op.outputs[key]
                            if hasattr(param, "value") and param.value is None:
                                param.value = Ref(father, key)

                if isinstance(other, list):
                    for item in other:
                        _set_wildcard_outputs(item)
                        current_graph.add_edge(item.name, self.name)
                elif hasattr(other, "name"):
                    _set_wildcard_outputs(other)
                    current_graph.add_edge(other.name, self.name)
                return self

        return self

    def __rlshift__(self, other):
        """op >> END (deprecated path, __rrshift__ handles this)."""
        if self == END:
            current_graph = get_current()
            if current_graph and hasattr(current_graph, "add_edge"):
                # Auto-set outputs: each output key -> father[key]
                def _set_wildcard_outputs(target_op):
                    if hasattr(target_op, "outputs") and not _has_explicit_outputs(target_op):
                        if target_op.outputs is None:
                            target_op.outputs = {}
                        father = getattr(target_op, "father", None) or PARENT
                        for key in target_op.outputs:
                            param = target_op.outputs[key]
                            if hasattr(param, "value") and param.value is None:
                                param.value = Ref(father, key)

                if isinstance(other, list):
                    for item in other:
                        _set_wildcard_outputs(item)
                        current_graph.add_edge(item.name, self.name)
                    return self
                elif hasattr(other, "name"):
                    _set_wildcard_outputs(other)
                    current_graph.add_edge(other.name, self.name)
                    return self
        return self

    def __gt__(self, other):
        """START > op or op > END (soft edge)."""
        if self == START:
            current_graph = get_current()
            if current_graph and hasattr(current_graph, "add_edge"):
                if isinstance(other, list):
                    for item in other:
                        current_graph.add_edge(self.name, item.name, soft=True)
                    return other
                elif hasattr(other, "name"):
                    current_graph.add_edge(self.name, other.name, soft=True)
                    return other
        return super().__gt__(other)

    def __rgt__(self, other):
        """[ops] > END (soft edge)."""
        current_graph = get_current()
        if current_graph and hasattr(current_graph, "add_edge"):
            if self == END:
                # Auto-set outputs: each output key -> father[key]
                def _set_wildcard_outputs(target_op):
                    if hasattr(target_op, "outputs") and not _has_explicit_outputs(target_op):
                        if target_op.outputs is None:
                            target_op.outputs = {}
                        father = getattr(target_op, "father", None) or PARENT
                        for key in target_op.outputs:
                            param = target_op.outputs[key]
                            if hasattr(param, "value") and param.value is None:
                                param.value = Ref(father, key)

                if isinstance(other, list):
                    for item in other:
                        _set_wildcard_outputs(item)
                        current_graph.add_edge(item.name, self.name, soft=True)
                elif hasattr(other, "name"):
                    _set_wildcard_outputs(other)
                    current_graph.add_edge(other.name, self.name, soft=True)
                return self
        return self


# Global dummy ops
START = DummyOp("__START__")
END = DummyOp("__END__")
PARENT = DummyOp("__PARENT__")
