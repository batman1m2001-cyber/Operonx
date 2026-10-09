"""``invoke`` — run an op or a graph from inside an op body.

Calling an ``@op`` function builds a graph node; it never runs the function
(inside an op body that is an error: see ``FuncOp``'s factory). ``invoke``
is how an op body runs one — an agent's tool, a helper graph per call::

    @op
    def lookup(order_id: str) -> dict:
        return {"status": ...}

    @op
    async def answer(order_id: str) -> dict:
        found = await invoke(lookup, order_id=order_id)     # {"status": ...}
        return {"text": found["status"]}

The target runs as a run of its own graph (a bare ``@op`` gets a graph of
one node). That run is a step of the caller's: its records join the
caller's trace under the calling op or ``child()`` step
(:mod:`operonx.core.nested`), so a viewer opens the step onto the graph.
Each target's engine is built once and reused.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict

__all__ = ["invoke"]

# target -> its engine; a target is a module-level @op / @graph function,
# so the cache lives as long as the code does
_ENGINES: Dict[Any, Any] = {}


async def invoke(target: Callable[..., Any], /, **inputs: Any) -> Dict[str, Any]:
    """Run ``target`` — an ``@op`` function or a ``@graph`` — on ``inputs``
    and return its outputs (``{"status": ...}``; a key with one value is the
    value, as ``Operon.run`` returns it).

    Inside an op body the run is a step of the caller's run, recorded under
    it. Outside one it is a run of its own.

    Raises:
        OpFailed: an op of the target failed (its engine runs with
            ``errors="raise"``: a step that failed is the caller's to
            handle, never a result with a key missing).
        TypeError: ``target`` is neither an ``@op`` function nor a
            ``@graph``.
    """
    engine = _ENGINES.get(target)
    if engine is None:
        engine = _ENGINES[target] = _engine_for(target)
    out = await engine.run(inputs)
    return {k: v for k, v in out.items() if not k.startswith("$")}


def _engine_for(target: Any) -> Any:
    from operonx.core.engine import Operon

    if getattr(target, "_operonx_graph", False):
        params = list(inspect.signature(target.__wrapped__).parameters)
        return Operon(target, params={p: None for p in params}, errors="raise")
    if callable(getattr(target, "configure", None)) and hasattr(target, "__wrapped__"):
        return Operon(_one_node(target), errors="raise")
    raise TypeError(
        f"invoke() runs an @op function or a @graph, got {target!r}. Decorate the function "
        "with @op, or call it directly."
    )


def _one_node(op_fn: Any) -> Any:
    """A graph of one node: ``op_fn`` on the graph's inputs, named after the
    function, its outputs the graph's."""
    from operonx.core.ops.base import END, PARENT, START
    from operonx.core.ops.graph.graph_op import GraphOp

    fn = op_fn.__wrapped__
    params = [
        name
        for name, p in inspect.signature(fn).parameters.items()
        if p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL)
    ]
    g = GraphOp(inputs={p: None for p in params} or None, name=fn.__name__)
    with g:
        named = {} if "name" in params else {"name": fn.__name__}  # `name` may be an input
        node = op_fn(**named, **{p: PARENT[p] for p in params})
        START >> node >> END
    return g
