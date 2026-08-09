"""Workflow state with Cell-based storage and O(1) index-based resolution."""

import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

from operonx.core.states.cell import DEFAULT_CONTEXT, Cell
from operonx.core.states.schema import StateSchema

__all__ = ["MemoryState", "ReducerError"]


class ReducerError(RuntimeError):
    """Raised when a reducer registered on a shared cell throws.

    Attributes:
        idx: cell index whose reducer failed
        old: value that was in the cell before the merge attempt
        new: incoming value that triggered the merge attempt
        cause: original exception raised by the reducer
    """

    def __init__(self, idx: int, old: Any, new: Any, cause: Exception) -> None:
        super().__init__(
            f"reducer on cell idx={idx} raised {type(cause).__name__}: {cause}. "
            f"old={old!r}, new={new!r}"
        )
        self.idx = idx
        self.old = old
        self.new = new
        self.cause = cause


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
        "_scratch",
        "_observers",
        "_custom_observers",
        "_interrupt_observers",
        "_interrupt_responses",
        "_current_step",
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

        # Free-form per-call scratch space. Last-write-wins. Read/written via
        # the `SCRATCH` accessor (operonx/core/ops/_edges.py) which resolves
        # to this dict via the `_current_state_var` ContextVar.
        self._scratch: Dict[str, Any] = {}

        # Observers subscribed to cell writes. Signature: (idx, ctx_key, value).
        # Called after each successful _write_cell — post-reducer, cell-committed.
        # Registered by engine.start() when a checkpointer/tracer is bound.
        # Kept empty (fast path) when no observer needs the stream.
        self._observers: List[Any] = []

        # Observers subscribed to EmitOp events. Signature: (op, ctx, channel, payload).
        # Fed via _notify_custom(); fast path when list is empty.
        self._custom_observers: List[Any] = []

        # Observers subscribed to InterruptOp events. Signature:
        # (op, ctx, payload, interrupt_id) → observer must arrange for
        # resume via state._resume_interrupt(interrupt_id, value).
        self._interrupt_observers: List[Any] = []

        # Response bus for InterruptOp — {interrupt_id: asyncio.Future}.
        # Lazily created via _register_interrupt_bus() on first suspend.
        self._interrupt_responses: Dict[str, Any] = None

        # Monotonic step counter for the checkpointer / tracer. Bumps once per
        # op invocation via ``advance_step()`` called from BaseOp.store_result
        # after that op's outputs have committed. All cell writes belonging to
        # the same op invocation share the same step_id — matches the plan's
        # "step_id per write batch" semantic (STATE_LOOP_REFACTOR_PLAN.md §Phase 2).
        self._current_step: int = 0

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

    def subscribe_writes(self, callback) -> None:
        """Register an observer to receive every cell write.

        The callback is invoked with ``(idx, ctx_key, value)`` after each
        successful write commits (post-reducer). Order is deterministic
        — observers fire in registration order.

        Args:
            callback: callable ``(idx, ctx_key, value) -> None``
        """
        self._observers.append(callback)

    def unsubscribe_writes(self, callback) -> None:
        """Remove a previously-registered observer. No-op if not registered."""
        try:
            self._observers.remove(callback)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Custom event bus (EmitOp)
    # ------------------------------------------------------------------

    def subscribe_custom(self, callback) -> None:
        """Register a callback for :class:`~operonx.EmitOp` events.

        Callback signature: ``(op_full_name, ctx, channel, payload) -> None``.
        Called synchronously from ``_notify_custom``; observers must not
        block. If no observer registers, EmitOp is a no-op (fire-and-forget
        drop, no back-pressure).
        """
        self._custom_observers.append(callback)

    def unsubscribe_custom(self, callback) -> None:
        """Remove a custom-event observer. No-op if not registered."""
        try:
            self._custom_observers.remove(callback)
        except ValueError:
            pass

    def _notify_custom(self, op, ctx, channel, payload) -> None:
        """Fire a CustomEvent to every subscribed observer. Fast path when none."""
        if not self._custom_observers:
            return
        for obs in self._custom_observers:
            obs(op, ctx, channel, payload)

    # ------------------------------------------------------------------
    # Interrupt bus (InterruptOp)
    # ------------------------------------------------------------------

    def subscribe_interrupt(self, callback) -> None:
        """Register a callback for :class:`~operonx.InterruptOp` events.

        Callback signature: ``(op_full_name, ctx, payload, interrupt_id) -> None``.
        Observer is responsible for arranging the resume value via
        ``state._interrupt_responses[interrupt_id] = value`` before the
        InterruptOp's core awaits it.
        """
        self._interrupt_observers.append(callback)

    def unsubscribe_interrupt(self, callback) -> None:
        """Remove an interrupt observer. No-op if not registered."""
        try:
            self._interrupt_observers.remove(callback)
        except ValueError:
            pass

    def _notify_interrupt(self, op, ctx, payload, interrupt_id) -> None:
        """Fire an InterruptEvent to every subscribed observer."""
        if not self._interrupt_observers:
            return
        for obs in self._interrupt_observers:
            obs(op, ctx, payload, interrupt_id)

    def _register_interrupt_bus(self) -> None:
        """Lazy-create the ``_interrupt_responses`` dict on first suspend."""
        if self._interrupt_responses is None:
            self._interrupt_responses = {}

    # ------------------------------------------------------------------
    # Step counter (checkpointer + tracer)
    # ------------------------------------------------------------------

    def advance_step(self) -> int:
        """Bump the monotonic step counter and return the new value.

        Called by ``BaseOp.store_result`` after an op's outputs have
        committed. Every cell write before this call shares the pre-bump
        step_id; subsequent writes see the new value.
        """
        self._current_step += 1
        return self._current_step

    def resume_interrupt(self, interrupt_id: str, value: Any) -> bool:
        """Resolve a pending InterruptOp with ``value``.

        Args:
            interrupt_id: id emitted with the InterruptEvent
            value: response the InterruptOp will surface as its ``response`` output

        Returns:
            True if a matching pending interrupt was resolved, False if unknown.
        """
        if self._interrupt_responses is None:
            return False
        fut = self._interrupt_responses.get(interrupt_id)
        if fut is None or fut.done():
            return False
        fut.set_result(value)
        return True

    def _write_cell(self, idx: int, ctx_key: tuple, value: Any) -> None:
        """Single funnel for all cell writes.

        Applies the cell's reducer if the cell is shared AND has one
        registered on the schema. Both ``__setitem__`` and the push-ref
        hop route through here so reducers never get bypassed.

        Reducer contract: ``(old, new) -> merged``. See
        ``operonx.reducers`` for the standard library. Reducer errors
        raise ``ReducerError`` wrapping the operands.

        Args:
            idx: cell index in ``self._cells``
            ctx_key: context tuple (``DEFAULT_CONTEXT`` for shared cells)
            value: incoming write; will be merged with current value if a
                reducer is registered
        """
        reducer = self.schema._reducers.get(idx)
        if reducer is not None:
            cell = self._cells[idx]
            old = cell[ctx_key] if ctx_key in cell else cell.default_value
            try:
                value = reducer(old, value)
            except Exception as e:  # noqa: BLE001 — re-raise wrapped
                raise ReducerError(idx=idx, old=old, new=value, cause=e) from e
        self._cells[idx][ctx_key] = value

        # Notify observers post-commit. Fast path (99% of runs): no observers.
        if self._observers:
            for obs in self._observers:
                obs(idx, ctx_key, value)

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

        self._write_cell(idx, ctx_key, value)

        # Push ref? Push 1 hop to target — route through _write_cell so
        # reducers apply on shared-cell targets too.
        push_ref = self.schema._push_refs[idx]
        if push_ref and push_ref.idx >= 0:
            self._write_cell(push_ref.idx, ctx_key, push_ref._fn(value))

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

    @property
    def scratch(self) -> Dict[str, Any]:
        """Per-call free-form scratch dict. Aliased identity, not a copy."""
        return self._scratch

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
