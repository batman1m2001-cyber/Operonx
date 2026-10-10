"""Which shape a graph has, and how one item becomes its inputs.

A graph is run in one of two shapes, and services and jobs agree on which:

- **with doors** — some op is ``@op(door="ingress")`` (the library
  ``ingress``, or a project's own, like a call's ``receive_audio``). The
  session's items go in through it and what ``egress`` sends is the
  answer. A stream (a call, a socket) needs this shape.
- **doorless** — no ingress op anywhere. The caller's data fills the
  graph's parameters by name, and the run's outputs are the answer: what
  ``Operon.run`` returns for the same inputs, without the ``$`` keys.

One rule for both, here, so a graph served on Monday and run over a file
on Tuesday is read the same way. Design: ``docs/DOORLESS_SERVICES_PLAN.md``.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any, Dict, Iterable, Optional, Sequence

__all__ = [
    "BindError",
    "RESERVED_QUERY",
    "bind_item",
    "has_doors",
    "plain",
    "serve_inputs",
]

#: Query names the serve layer reads itself (a run's id, a webhook's
#: callback and thread, a stream reconnect). They never reach a doorless
#: graph unless it has a parameter of that name.
RESERVED_QUERY = ("trace_id", "callback", "thread_id", "run_id", "after_seq")


class BindError(ValueError):
    """An item does not fit the graph's parameters. ``field`` names the
    parameter or item field at fault, when there is one."""

    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.field = field


def _graph_of(target: Any) -> Any:
    """The ``GraphOp`` of an ``Operon``, a ``GraphOp``, or a module-level
    ``@graph`` (built with every parameter left open)."""
    graph = getattr(target, "graph", None)
    if graph is not None and hasattr(graph, "_ops"):
        return graph
    if hasattr(target, "_ops"):
        return target
    if callable(target) and getattr(target, "_operonx_graph", False):
        try:
            params = {p: None for p in inspect.signature(target).parameters}
        except (TypeError, ValueError):
            params = {}
        return target(name=target.__name__, **params)
    raise TypeError(f"{type(target).__name__} is not a graph")


def has_doors(target: Any) -> bool:
    """True when any op in the graph, at any depth, is an ingress door
    (``op.door == "ingress"``). Takes an ``Operon``, a ``GraphOp`` or a
    module-level ``@graph``; the answer is kept on a compiled ``Operon``."""
    cached = getattr(target, "_doors", None) if hasattr(target, "graph") else None
    if cached is not None:
        return cached

    def walk(g: Any) -> bool:
        for op in (getattr(g, "_ops", None) or {}).values():
            if getattr(op, "door", None) == "ingress" or walk(op):
                return True
        return False

    found = walk(_graph_of(target))
    if hasattr(target, "graph"):
        try:
            target._doors = found
        except AttributeError:  # a slotted object: just not cached
            pass
    return found


def plain(out: Any) -> Any:
    """A run's outputs without the engine's ``$`` keys — a doorless graph's
    answer."""
    if isinstance(out, dict):
        return {k: v for k, v in out.items() if not str(k).startswith("$")}
    return out


def bind_item(
    params: Sequence[str],
    item: Any,
    fixed: Optional[Mapping[str, Any]] = None,
    input: Optional[str] = None,  # noqa: A002 — the parameter's name
    defaults: Iterable[str] = (),
) -> Dict[str, Any]:
    """The run inputs for one item of a doorless graph.

    ``fixed`` inputs come first. Then: with ``input``, the whole item goes
    to that parameter; a mapping fills parameters by name (a field the
    graph does not take is refused); anything else goes to the only free
    parameter — the only one without a default (``defaults``) when several
    are free.
    """
    inputs = dict(fixed or {})
    params = list(params)
    if input is not None:
        if input not in params:
            raise BindError(
                f"input={input!r}, but the graph takes {params or 'no parameters'}", field=input
            )
        inputs[input] = item
        return inputs
    if isinstance(item, Mapping):
        unknown = [k for k in item if k not in params]
        if unknown:
            raise BindError(
                f"the item has {unknown}, which the graph does not take "
                f'(it takes {params or "nothing"}); pass input="<param>" to hand it '
                "the whole item",
                field=str(unknown[0]),
            )
        inputs.update(item)
        return inputs
    free = [p for p in params if p not in inputs]
    if len(free) > 1:
        required = [p for p in free if p not in set(defaults)]
        if len(required) == 1:
            free = required
    if len(free) != 1:
        raise BindError(
            f"a {type(item).__name__} item needs one graph parameter to go to; "
            f'the graph has {free or "none"} free — pass input="<param>"'
        )
    inputs[free[0]] = item
    return inputs


def serve_inputs(
    params: Sequence[str],
    given: Mapping[str, Any],
    item: Any,
    *,
    defaults: Optional[Mapping[str, Any]] = None,
    tick: bool = False,
    reserved: Iterable[str] = (),
) -> Dict[str, Any]:
    """The run inputs a served doorless graph gets for one request.

    ``given`` comes from the connection — its query string, or what an
    ``on_session`` hook built; names in ``reserved`` that are not
    parameters are dropped from it. ``item`` is the request's body, or a
    schedule's tick (``tick=True``: only its fields the graph takes as
    parameters are kept, the rest dropped). A name in both ``given`` and
    the body is refused, and so is a parameter that neither sets and that
    has no default. Defaults fill what is left.
    """
    params = list(params)
    defaults = dict(defaults or {})
    skip = set(reserved) - set(params)
    given = {k: v for k, v in given.items() if k not in skip}
    unknown = [k for k in given if k not in params]
    if unknown:
        raise BindError(
            f"{unknown[0]!r} is not a parameter of the graph (it takes {params or 'nothing'})",
            field=str(unknown[0]),
        )
    if tick:
        fields = item if isinstance(item, Mapping) else {}
        inputs = dict(given)
        inputs.update({k: v for k, v in fields.items() if k in params and k not in given})
    elif item is None or (isinstance(item, Mapping) and not item):
        inputs = dict(given)
    else:
        if isinstance(item, Mapping):
            both = [k for k in item if k in given]
            if both:
                raise BindError(
                    f"{both[0]!r} is given in both the query and the body; give it once",
                    field=str(both[0]),
                )
        inputs = bind_item(params, item, fixed=given, defaults=defaults)
    for name in params:
        if name not in inputs and name in defaults:
            inputs[name] = defaults[name]
    missing = [p for p in params if p not in inputs]
    if missing:
        raise BindError(f"missing required parameter {missing[0]!r}", field=missing[0])
    return inputs
