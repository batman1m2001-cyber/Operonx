"""Edge classes, DummyOp, and global singletons (START, END, PARENT, SCRATCH).

Provides the edge-connectivity classes and sentinel ops used by the
``>>`` / ``>`` operator syntax for wiring ops in a GraphOp.
"""

from typing import TYPE_CHECKING

from operonx.core.ops._utils import _set_wildcard_outputs
from operonx.core.ops.base import BaseOp
from operonx.core.states._scratch_var import _current_state_var
from operonx.core.states.scratch_ref import ScratchRef
from operonx.core.utils.context import get_current

if TYPE_CHECKING:
    from operonx.core.ops.base import BaseOp as _BaseOp


class SoftEdge:
    """Marker for soft-edge connection. Use ``~op`` syntax."""

    __slots__ = ["op"]

    def __init__(self, op: "_BaseOp"):
        self.op = op

    @property
    def name(self) -> str:
        return self.op.name

    def __rrshift__(self, other):
        add_edge = getattr(self.op.parent, "add_edge", None)
        if isinstance(other, list):
            if add_edge is not None:
                for item in other:
                    edge_type = "condition" if getattr(item, "type", None) == "branch" else "normal"
                    add_edge(item.name, self.op.name, edge_type, soft=True)
            return self.op
        elif getattr(other, "name", None) is not None:
            edge_type = "condition" if getattr(other, "type", None) == "branch" else "normal"
            if add_edge is not None:
                add_edge(other.name, self.op.name, edge_type, soft=True)
            return self.op
        return NotImplemented

    def __rshift__(self, other):
        return self.op.__rshift__(other)


class DummyOp(BaseOp):
    """Sentinel op used as START, END, and PARENT markers."""

    type = "dummy"

    def __init__(self, name: str):
        super().__init__(name=name)

    def declare(self, *, reducers=None, **vars):
        """Declare shared vars on the current graph, with optional reducers.

        Shared vars persist across all stream contexts within the graph. Normal
        PARENT vars are copied per stream context. When a reducer is registered
        for a var, concurrent/repeated writes to its shared cell go through
        ``reducer(old, new)`` instead of overwriting. Without a reducer, shared
        cell writes use last-write-wins semantics.

        Args:
            reducers: Optional ``{var_name: fn}`` mapping. Each ``fn`` is a
                ``(old, new) -> merged`` reducer applied at cell write time.
                Every key MUST be one of the vars declared in this call
                (build-time check).
            **vars: ``var_name=initial_value`` declarations. Same semantics as
                ``PARENT.shared()``.

        Raises:
            TypeError: if called on anything other than PARENT.
            RuntimeError: if called outside a ``@graph`` body.
            ValueError: if a reducer key is not in the declared vars.

        Usage::

            from operonx.reducers import add_messages

            @graph
            def agent():
                PARENT.declare(
                    count=0,
                    messages=[],
                    reducers={"messages": add_messages},
                )
        """
        if self.name != "__PARENT__":
            raise TypeError("declare() can only be called on PARENT")
        current_graph = get_current()
        if current_graph is None:
            raise RuntimeError("PARENT.declare() must be called inside a @graph function body")

        reducers = reducers or {}
        if not isinstance(reducers, dict):
            raise TypeError(f"declare() reducers must be a dict, got {type(reducers).__name__}")

        # E4: reducer keys must reference declared vars
        unknown = set(reducers) - set(vars)
        if unknown:
            raise ValueError(
                f"declare() reducers reference undeclared vars: {sorted(unknown)}. "
                f"declared vars: {sorted(vars)}"
            )

        if not hasattr(current_graph, "_shared_vars"):
            current_graph._shared_vars = {}
        current_graph._shared_vars.update(vars)

        if not hasattr(current_graph, "_reducer_vars"):
            current_graph._reducer_vars = {}
        current_graph._reducer_vars.update(reducers)

    # NOTE (1.0.0): ``PARENT.shared(**vars)`` was removed. Use
    # ``PARENT.declare(**vars, reducers={...})`` instead — same shared-cell
    # semantics plus optional reducers on fan-in writes.

    def __rshift__(self, other):
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
        if self == END:
            current_graph = get_current()
            if current_graph and hasattr(current_graph, "add_edge"):
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
        current_graph = get_current()
        if current_graph and hasattr(current_graph, "add_edge"):
            if self == END:
                if isinstance(other, list):
                    for item in other:
                        _set_wildcard_outputs(item)
                        current_graph.add_edge(item.name, self.name, soft=True)
                elif hasattr(other, "name"):
                    _set_wildcard_outputs(other)
                    current_graph.add_edge(other.name, self.name, soft=True)
                return self
        return self


# Global sentinel ops
START = DummyOp("__START__")
END = DummyOp("__END__")
PARENT = DummyOp("__PARENT__")


class ScratchAccessor:
    """Dict-like accessor for per-call scratch space.

    - Inside an op body (ContextVar bound): reads/writes the live scratch
      dict on the current MemoryState.
    - At graph-construction time (ContextVar unbound): ``__getitem__`` returns
      a ``ScratchRef`` marker, post-resolved by ``BaseOp.get_inputs()``.
      ``__setitem__`` raises — write-outside-run is a programming error.
    """

    __slots__ = ()

    def __getitem__(self, key: str):
        try:
            state = _current_state_var.get()
        except LookupError:
            return ScratchRef(key)
        return state._scratch.get(key)

    def __setitem__(self, key: str, value) -> None:
        try:
            state = _current_state_var.get()
        except LookupError as e:
            raise RuntimeError(
                f"SCRATCH[{key!r}] = ... called outside an active run. "
                "Write SCRATCH inside an @op body, or seed via "
                "engine.start(scratch={...})."
            ) from e
        state._scratch[key] = value
        # Phase 2b3 B1: fire the scratch bus so checkpointers/tracers see
        # SCRATCH mutations too. Fast path when no observer subscribed.
        state._notify_scratch(key, value)

    def __delitem__(self, key: str) -> None:
        try:
            state = _current_state_var.get()
        except LookupError as e:
            raise RuntimeError(f"del SCRATCH[{key!r}] called outside an active run.") from e
        state._scratch.pop(key, None)

    def __contains__(self, key: str) -> bool:
        try:
            state = _current_state_var.get()
        except LookupError:
            return False
        return key in state._scratch


SCRATCH = ScratchAccessor()
