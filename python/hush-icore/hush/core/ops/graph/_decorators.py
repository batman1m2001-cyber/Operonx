"""@graph decorator for building reusable GraphOp factories.

If the caller passes `until=` kwarg, the graph becomes a loop (GraphOp.loop).
Otherwise it's a simple one-shot graph (GraphOp).

Example — simple graph::

    @graph
    def classify(speech):
        ...
        START >> prompt >> llm >> parser >> END

    g = classify(speech=PARENT["speech"])

Example — loop graph (pass `until=`)::

    @graph
    def counter(count, until, max_iterations=5):
        inc = increment(counter=count)
        inc["counter"] >> PARENT["count"]
        START >> inc >> END

    g = counter(count=0, until="count >= 5", max_iterations=5)
"""

import inspect
from functools import wraps

from hush.core.loggings import LOGGER
from hush.core.ops._shortcuts import _BASE_INIT_KEYS, split_shorthand_kwargs
from hush.core.ops.base import PARENT
from hush.core.states.ref import Ref
from hush.core.utils.auto_name import register_skip

# Keys consumed by loop logic — popped from kwargs before graph creation
_LOOP_KEYS = {"until", "max_iterations"}


def _build_fn_args(input_mappings, param_names):
    """Build function arguments from input mappings.

    Ref values, bare PARENT, and None placeholders become PARENT[key] refs
    (resolved at runtime from graph inputs). Static values are passed
    through as-is for use at graph build time.

    None is treated as a placeholder for "provide at engine.start() time",
    enabling graph reuse: Hush(graph, params={"x": None}) compiles once,
    engine.start(inputs={"x": real_value}) per call.
    """
    args = {}
    for key, value in input_mappings.items():
        if key not in param_names:
            continue
        if isinstance(value, Ref) or value is PARENT or value is None:
            args[key] = PARENT[key]
        else:
            args[key] = value
    return args


def graph(fn=None, *, bound: "str | None" = None):
    """Decorator to turn a builder function into a reusable GraphOp factory.

    Can be used bare or with keyword arguments::

        @graph
        def pipeline(x):
            ...

        @graph(bound="io")
        def io_pipeline(x):
            ...

    The function's parameters become graph inputs. Ref values are injected as
    PARENT refs; static values are passed through directly for use at build time.

    If the caller passes ``until=`` kwarg, the graph becomes a feedback loop::

        @graph
        def ask(error="init", until="error == None", max_iterations=2, ...):
            ...

        # Simple (no loop):
        a = ask(fields=["result: str"], speech="hello")

        # With retry loop:
        a = ask(fields=["result: str"], until="error == None", max_iterations=3, speech="hello")

    ``max_iterations`` can be a string expression evaluated against kwargs::

        a = ask(retry=2, until="error == None", max_iterations="retry + 1", ...)

    Args:
        bound: Execution bound for the graph. ``None`` auto-detects from children.
            ``"sync"`` forces inline dispatch, ``"io"``/``"cpu"`` forces task dispatch.
    """

    def _make_graph_wrapper(fn, graph_bound):
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

        param_names = set(sig.parameters.keys()) - _LOOP_KEYS

        @wraps(fn)
        def wrapper(**kwargs):
            # Pop loop config from kwargs
            until = kwargs.pop("until", None)
            max_iterations = kwargs.pop("max_iterations", 100)

            input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)

            # Inject decorator-level bound if not overridden at call time
            if graph_bound is not None and "bound" not in init_kwargs:
                init_kwargs["bound"] = graph_bound

            if until is not None:
                # ── Loop mode ──
                loop_params = set()
                config_params = set()
                for name, param in sig.parameters.items():
                    if name in _LOOP_KEYS:
                        continue
                    if param.kind == inspect.Parameter.KEYWORD_ONLY:
                        config_params.add(name)
                    elif param.kind not in (
                        inspect.Parameter.VAR_KEYWORD,
                        inspect.Parameter.VAR_POSITIONAL,
                    ):
                        loop_params.add(name)

                loop_state = {}
                config = {}
                remaining_inputs = {}
                for k, v in (input_mappings or {}).items():
                    if isinstance(v, Ref) or v is PARENT:
                        remaining_inputs[k] = v
                    elif k in loop_params:
                        loop_state[k] = v
                    elif k in config_params:
                        config[k] = v
                    else:
                        remaining_inputs[k] = v  # template vars etc
                for k, v in init_kwargs.items():
                    if k in loop_params:
                        loop_state[k] = v
                    elif k in config_params:
                        config[k] = v

                # Resolve max_iterations if string expression
                if isinstance(max_iterations, str):
                    defaults = {
                        name: p.default
                        for name, p in sig.parameters.items()
                        if p.default is not inspect.Parameter.empty
                    }
                    _max = eval(max_iterations, {}, {**defaults, **loop_state, **config})  # noqa: S307
                else:
                    _max = max_iterations

                g = GraphOp.loop(until=until, max_iterations=_max, **loop_state)
                g.inputs.update(remaining_inputs or {})
                with g:
                    fn_args = {k: PARENT[k] for k in loop_state if k in loop_params}
                    fn_args.update(config)
                    fn_args.update(_build_fn_args(remaining_inputs, param_names))
                    fn(**fn_args)
            else:
                # ── Simple mode ──
                g = GraphOp(inputs=input_mappings or None, **init_kwargs)
                with g:
                    fn_args = _build_fn_args(input_mappings, param_names)
                    fn(**fn_args)

            return g

        register_skip(wrapper)
        wrapper.__wrapped__ = fn
        return wrapper

    if fn is not None:
        # @graph without parentheses
        return _make_graph_wrapper(fn, bound)
    # @graph(bound="io") with parentheses
    return lambda f: _make_graph_wrapper(f, bound)


# Backward compat: @graph.loop still works
def _graph_loop(until=None, max_iterations=100):
    """Deprecated: use @graph with until= kwarg instead."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(**kwargs):
            kwargs["until"] = until
            kwargs["max_iterations"] = max_iterations
            return graph(fn)(**kwargs)

        register_skip(wrapper)
        wrapper.__wrapped__ = fn
        return wrapper

    return decorator


graph.loop = _graph_loop
