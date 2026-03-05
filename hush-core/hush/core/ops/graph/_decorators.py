"""@graph and @graph.loop decorators for building reusable GraphOp factories."""

import inspect
from functools import wraps

from hush.core.loggings import LOGGER
from hush.core.ops._shortcuts import _BASE_INIT_KEYS, split_shorthand_kwargs
from hush.core.ops.base import PARENT
from hush.core.utils.auto_name import register_skip


def graph(fn):
    """Decorator to turn a builder function into a reusable GraphOp factory.

    The function's parameters become graph inputs, injected as PARENT refs.
    Auto-naming works through the decorator (via register_skip).

    Example::

        @graph
        def verify_card(conversation):
            check = detect_card(conversation=conversation)
            START >> check >> END

        g1 = verify_card(conversation=other_op["conv"])
        # g1.name == "g1"
    """
    from hush.core.ops.graph.graph_op import GraphOp

    sig = inspect.signature(fn)
    collisions = set(sig.parameters.keys()) & _BASE_INIT_KEYS
    if collisions:
        LOGGER.warning(
            "@graph function '%s' has parameter(s) %s that collide with reserved op keywords %s. "
            "Consider renaming them.",
            fn.__name__,
            sorted(collisions),
            sorted(_BASE_INIT_KEYS),
        )

    param_names = set(sig.parameters.keys())

    @wraps(fn)
    def wrapper(**kwargs):
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        g = GraphOp(inputs=input_mappings or None, **init_kwargs)
        with g:
            parent_refs = {key: PARENT[key] for key in input_mappings if key in param_names}
            fn(**parent_refs)
        return g

    register_skip(wrapper)
    wrapper.__wrapped__ = fn
    return wrapper


def _graph_loop(until=None, max_iterations=100):
    """Decorator factory for feedback-loop graphs.

    Example::

        @graph.loop(until="count >= 5")
        def counter(count):
            inc = increment(counter=count)
            inc["counter"] >> PARENT["count"]
            START >> inc >> END

        loop = counter(count=0)
    """
    from hush.core.ops.graph.graph_op import GraphOp

    def decorator(fn):
        sig = inspect.signature(fn)
        param_names = set(sig.parameters.keys())

        @wraps(fn)
        def wrapper(**kwargs):
            input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
            g = GraphOp.loop(until=until, max_iterations=max_iterations, **init_kwargs)
            g.inputs.update(input_mappings or {})
            with g:
                parent_refs = {k: PARENT[k] for k in input_mappings if k in param_names}
                fn(**parent_refs)
            return g

        register_skip(wrapper)
        wrapper.__wrapped__ = fn
        return wrapper

    return decorator


graph.loop = _graph_loop
