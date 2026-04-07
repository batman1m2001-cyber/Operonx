"""Edge classes, DummyOp, and global singletons (START, END, PARENT).

Provides the edge-connectivity classes and sentinel ops used by the
``>>`` / ``>`` operator syntax for wiring ops in a GraphOp.
"""

from typing import TYPE_CHECKING

from hush.core.ops._utils import _set_wildcard_outputs
from hush.core.ops.base import BaseOp
from hush.core.utils.context import get_current

if TYPE_CHECKING:
    from hush.core.ops.base import BaseOp as _BaseOp


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

    def shared(self, **kwargs):
        """Declare shared vars on current graph. Only valid on PARENT.

        Shared vars persist across all stream contexts within the graph.
        Normal PARENT vars are copied per stream context.

        Usage::

            @graph
            def pipeline():
                PARENT.shared(current_state="REMINDER", history=[])
                # PARENT["current_state"] now shared across all stream contexts
        """
        if self.name != "__PARENT__":
            raise TypeError("shared() can only be called on PARENT")
        current_graph = get_current()
        if current_graph is None:
            raise RuntimeError("PARENT.shared() must be called inside a @graph function body")
        if not hasattr(current_graph, "_shared_vars"):
            current_graph._shared_vars = {}
        current_graph._shared_vars.update(kwargs)

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
