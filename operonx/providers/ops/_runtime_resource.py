"""``resource=`` as a graph input: the model or store a provider op uses, chosen per run.

``LLMOp.of(resource="gpt-4o")`` fixes the resource when the graph is built. Given a graph input
or any other ``Ref`` instead (``LLMOp.of(resource=model, ...)`` in ``@graph def chat(model, q)``),
the key arrives with each call: the op runs that call on a copy of itself bound to the key, one
copy per key, each resolving and caching its own backend. Nothing on the op is mutated per call,
so concurrent calls with different keys do not meet. This is what lets a graph that serves many
models or stores be one module-level ``@graph`` instead of a graph generated per resource.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, Optional, Tuple

from operonx.core.states.ref import Ref
from operonx.core.utils.common import Param

__all__ = ["take_resource", "per_call", "RESOURCE_PARAM"]

#: The input a runtime resource arrives on.
RESOURCE_PARAM = Param(type=str, required=True)


def take_resource(
    resource: Any, inputs: Optional[Dict[str, Any]]
) -> Tuple[Any, Optional[dict], bool]:
    """``(static resource, inputs, dynamic)``: a ``Ref`` resource becomes the ``resource`` input."""
    if isinstance(resource, Ref):
        merged = dict(inputs or {})
        merged["resource"] = resource
        return None, merged, True
    return resource, inputs, False


def _shallow_copy(op: Any) -> Any:
    """A new op of the same class sharing every slot value (``copy.copy`` refuses an op: a
    subclass's class attribute, such as ``type``, shadows a ``BaseOp`` slot)."""
    clone = object.__new__(type(op))
    for klass in type(op).__mro__:
        for name in getattr(klass, "__slots__", ()):
            try:
                setattr(clone, name, getattr(op, name))
            except AttributeError:  # unset, or read-only on the instance
                pass
    if hasattr(op, "__dict__"):
        clone.__dict__.update(op.__dict__)
    return clone


def _bound(op: Any, key: Optional[str]) -> Any:
    if not key:
        raise ValueError(
            f"Op '{op.name}': resource= is a graph input and this call gave none; pass the "
            "resource key (e.g. 'gpt-4o') as that input"
        )
    found = op._bound.get(key)
    if found is None:
        found = _shallow_copy(op)
        found.resource = key
        found._dynamic = False
        found._bound = {}
        found._initialized = False
        op._bound[key] = found
    return found


def per_call(op: Any, method: str):
    """``op``'s core for a runtime resource: each call runs ``method`` on the copy bound to its key."""
    if inspect.isasyncgenfunction(getattr(type(op), method)):

        async def core(resource: Optional[str] = None, **inputs):
            async for item in getattr(_bound(op, resource), method)(**inputs):
                yield item

    else:

        async def core(resource: Optional[str] = None, **inputs):
            return await getattr(_bound(op, resource), method)(**inputs)

    return core
