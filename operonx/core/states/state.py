"""Workflow state with Cell-based storage and O(1) index-based resolution."""

import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

from operonx.core.states.cell import DEFAULT_CONTEXT, Cell
from operonx.core.states.schema import StateSchema

__all__ = ["MemoryState"]

_uuid4 = uuid.uuid4


class MemoryState:
    """Workflow state with Cell-based storage and O(1) indexed access.

    Design:
        - Read pulls (1 hop): If input ref exists, pull from source and cache
        - Write pushes (1 hop): If output ref exists, push to target
        - No recursion, no magic

    Data flow:
        __setitem__: Store value, push 1 hop if output ref exists
        __getitem__: Return cached value, or pull 1 hop if input ref exists

    Supports both 2-tuple and 3-tuple access:
        state[op, var]          # default context
        state[op, var, ctx]     # explicit context
    """

    __slots__ = (
        "schema",
        "_cells",
        "_user_id",
        "_session_id",
        "_request_id",
        "_tags",
        "tracing",
        "_iter_labels",
    )

    def __init__(
        self,
        schema: StateSchema,
        inputs: Dict[str, Any] = None,
        user_id: str = None,
        session_id: str = None,
        request_id: str = None,
    ) -> None:
        """Initialize MemoryState.

        Args:
            schema: StateSchema defining the state structure
            inputs: Initial input values for the workflow
            user_id: User ID (auto-generated if not provided)
            session_id: Session ID (auto-generated if not provided)
            request_id: Request ID (auto-generated if not provided)
        """
        self.schema = schema
        self._cells: List[Cell] = [
            Cell(v, is_shared=(idx in schema._shared_indices))
            for idx, v in enumerate(schema._defaults)
        ]
        self._user_id = user_id or str(_uuid4())
        self._session_id = session_id or str(_uuid4())
        self._request_id = request_id or str(_uuid4())

        # Dynamic tags collected during execution
        self._tags: List[str] = []
        self.tracing = True  # default on; engine sets False when no tracer

        # Per-execution store for operonx.core.tracing.label(). Keyed by
        # (gen_op_full_name, ctx) → {"labels": [str], "next_idx": int}.
        # Populated by label() calls inside generator ops, drained by
        # TraceCollector when building stream_context synthetics.
        self._iter_labels: Dict[Tuple[str, tuple], Dict[str, Any]] = {}

        # Apply initial inputs
        if inputs:
            for var, value in inputs.items():
                idx = schema.get_index(schema.name, var)
                if idx >= 0:
                    self._cells[idx][DEFAULT_CONTEXT] = value

    # =========================================================================
    # Core API: Simple and predictable
    # =========================================================================

    @staticmethod
    def _unpack_key(key) -> Tuple[str, str, tuple]:
        """Unpack 2-tuple or 3-tuple key into (op, var, ctx_key)."""
        if len(key) == 2:
            return key[0], key[1], DEFAULT_CONTEXT
        op, var, ctx = key
        if ctx is None:
            return op, var, DEFAULT_CONTEXT
        # Accept string context for backwards compat
        if isinstance(ctx, str):
            return op, var, (ctx,)
        return op, var, ctx

    def __setitem__(
        self, key: Union[Tuple[str, str], Tuple[str, str, Optional[str]]], value: Any
    ) -> None:
        """Store value. Push to target if push_ref exists (1 hop only).

        Args:
            key: (op, var) or (op, var, ctx)
            value: Value to store
        """
        op, var, ctx_key = self._unpack_key(key)
        idx = self.schema.get_index(op, var)
        if idx < 0:
            raise KeyError(f"({op}, {var}) not found in schema")

        self._cells[idx][ctx_key] = value

        # Push ref? Push 1 hop to target
        push_ref = self.schema._push_refs[idx]
        if push_ref and push_ref.idx >= 0:
            self._cells[push_ref.idx][ctx_key] = push_ref._fn(value)

    def __getitem__(self, key: Union[Tuple[str, str], Tuple[str, str, Optional[str]]]) -> Any:
        """Get value. Pull from source if pull_ref exists (1 hop only).

        Args:
            key: (op, var) or (op, var, ctx)

        Returns:
            Value at (op, var, ctx) or None if not found
        """
        op, var, ctx_key = self._unpack_key(key)
        idx = self.schema.get_index(op, var)
        if idx < 0:
            return None

        cell = self._cells[idx]

        # For shared cells, always use Cell.__getitem__ which maps to DEFAULT_CONTEXT
        if cell.is_shared:
            return cell[ctx_key]

        # Has cached value? Return it
        if ctx_key in cell:
            return cell[ctx_key]

        # Pull ref? Pull 1 hop from source and cache.
        # Cell.__getitem__ walks up the context hierarchy automatically,
        # so source_cell[("main", "[0]")] finds batch values at ("main",).
        pull_ref = self.schema._pull_refs[idx]
        if pull_ref and not pull_ref.is_output and pull_ref.idx >= 0:
            source_cell = self._cells[pull_ref.idx]
            source_val = source_cell[ctx_key]
            if source_val is not None or source_cell.default_value is not None:
                result = pull_ref._fn(source_val)
                cell[ctx_key] = result  # Cache
                return result

        # No value - return default
        return cell.default_value

    def get(self, op: str, var: str, ctx=None) -> Any:
        """Get value with explicit parameters."""
        return self[op, var, ctx]

    def get_cell(self, op: str, var: str) -> Cell:
        """Get the Cell object for a variable."""
        idx = self.schema.get_index(op, var)
        if idx < 0:
            raise KeyError(f"({op}, {var}) not found in schema")
        return self._cells[idx]

    def has(self, op: str, var: str, ctx=None) -> bool:
        """Check if value exists (without resolving ref)."""
        idx = self.schema.get_index(op, var)
        if idx < 0:
            return False
        if ctx is None:
            ctx_key = DEFAULT_CONTEXT
        elif isinstance(ctx, str):
            ctx_key = (ctx,)
        else:
            ctx_key = ctx
        return ctx_key in self._cells[idx]

    # =========================================================================
    # Index-based Access (O(1)) - Raw access without ref resolution
    # =========================================================================

    def get_by_index(self, idx: int, ctx: Optional[str] = None) -> Any:
        """Direct cell access by index (no ref resolution)."""
        if 0 <= idx < len(self._cells):
            return self._cells[idx][ctx]
        raise IndexError(f"Index {idx} out of range")

    def set_by_index(self, idx: int, value: Any, ctx: Optional[str] = None) -> None:
        """Direct cell assignment by index (no ref push)."""
        if 0 <= idx < len(self._cells):
            self._cells[idx][ctx] = value
        else:
            raise IndexError(f"Index {idx} out of range")

    # =========================================================================
    # Tracing Support
    # =========================================================================

    def iter_executed(self, op_name: str):
        """Yield (context_id, start_time) for each execution of op_name.

        Uses duration_ms as the execution marker (always set), then looks up
        start_time for context. start_time may be None when tracing is off.
        """
        dur_idx = self.schema.get_index(op_name, "duration_ms")
        if dur_idx < 0:
            return
        st_idx = self.schema.get_index(op_name, "start_time")
        for ctx, dur in self._cells[dur_idx].items():
            if dur is not None:
                start = self._cells[st_idx][ctx] if st_idx >= 0 else None
                yield ctx, start

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def name(self) -> str:
        """Workflow name."""
        return self.schema.name

    @property
    def metadata(self) -> Dict[str, Any]:
        """State metadata including user_id, session_id, request_id."""
        return {
            "user_id": self._user_id,
            "session_id": self._session_id,
            "request_id": self._request_id,
        }

    @property
    def user_id(self) -> str:
        """User ID."""
        return self._user_id

    @property
    def session_id(self) -> str:
        """Session ID."""
        return self._session_id

    @property
    def request_id(self) -> str:
        """Request ID."""
        return self._request_id

    @property
    def tags(self) -> List[str]:
        """Dynamic tags collected during execution."""
        return self._tags.copy()

    def add_tag(self, tag: str) -> None:
        """Add a dynamic tag to this execution.

        Tags are used for filtering/grouping traces in observability tools.
        Duplicate tags are ignored.

        Args:
            tag: Tag string to add (e.g., "error", "cache-hit", "fallback")
        """
        if tag not in self._tags:
            self._tags.append(tag)

    def add_tags(self, tags: List[str]) -> None:
        """Add multiple dynamic tags to this execution.

        Args:
            tags: List of tag strings to add
        """
        for tag in tags:
            self.add_tag(tag)

    # =========================================================================
    # Collection Interface
    # =========================================================================

    def __contains__(self, key: Tuple[str, str]) -> bool:
        """Check if (op, var) exists in schema."""
        return key in self.schema

    def __len__(self) -> int:
        """Number of cells."""
        return len(self._cells)

    def __iter__(self):
        """Iterate over (op, var) pairs."""
        return iter(self.schema)

    # =========================================================================
    # Context Manager and Utilities
    # =========================================================================

    def __enter__(self) -> "MemoryState":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}', cells={len(self._cells)})"

    def __hash__(self) -> int:
        return hash(self._request_id)

    def __eq__(self, other) -> bool:
        if not isinstance(other, MemoryState):
            return False
        return self._request_id == other._request_id

    def show(self) -> None:
        """Display debug view of current state values."""
        print(f"\n=== {self.__class__.__name__}: {self.name} ===")

        for op, var in self.schema:
            idx = self.schema.get_index(op, var)
            cell = self._cells[idx]
            pull_ref = self.schema._pull_refs[idx]

            if not cell.contexts:
                if pull_ref:
                    print(f"{op}.{var} -> pull_ref[{pull_ref.idx}] (no value yet)")
                else:
                    print(f"{op}.{var} -> {cell.default_value}")
            elif len(cell.contexts) == 1:
                ctx = next(iter(cell.contexts))
                value = cell.contexts[ctx]
                value_str = repr(value)[:50] + "..." if len(repr(value)) > 50 else repr(value)
                print(f"{op}.{var} [{ctx}] = {value_str}")
            else:
                print(f"{op}.{var}:")
                for ctx, value in cell.contexts.items():
                    value_str = repr(value)[:50] + "..." if len(repr(value)) > 50 else repr(value)
                    print(f"  [{ctx}] = {value_str}")
