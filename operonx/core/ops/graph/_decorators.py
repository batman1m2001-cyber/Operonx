"""@graph decorator for building reusable GraphOp factories.

The decorator turns a builder function into a factory that constructs a
fresh ``GraphOp`` on every call. Function parameters become graph inputs;
``Ref`` values are injected as ``PARENT[key]`` refs, static values pass
through as-is.

Example::

    @graph
    def classify(speech):
        prompt = build_prompt(text=speech)
        response = llm(prompt=prompt)
        result = parse(response=response["content"])
        START >> prompt >> response >> result >> END

    g = classify(speech=PARENT["speech"])

Note (1.0.0): the ``@graph(until=..., max_iterations=...)`` retry-loop
sugar was removed. Two replacements depending on what you were doing:

  * For LLM parse/validate retry, use ``LLMOp(fields=..., max_retries=...)``.
    Retry lives inside the op, no graph-shape overhead.
  * For general control-flow loops, write a back-edge in the ``@graph``
    body — the Phase 3 cycle-rewrite pass synthesizes the loop for you::

        @graph
        def counter():
            inc = increment(counter=PARENT["count"])
            inc["counter"] >> PARENT["count"]
            START >> inc >> if_(PARENT["count"] >= 5, END).else_(inc)
"""

from functools import wraps

from operonx.core.loggings import LOGGER
from operonx.core.ops._shortcuts import _BASE_INIT_KEYS, split_shorthand_kwargs
from operonx.core.ops.base import PARENT
from operonx.core.states.ref import Ref
from operonx.core.utils.auto_name import register_skip


def _build_fn_args(input_mappings, param_names):
    """Build function arguments from input mappings.

    Ref values, bare PARENT, and None placeholders become PARENT[key] refs
    (resolved at runtime from graph inputs). Static values are passed
    through as-is for use at graph build time.

    None is treated as a placeholder for "provide at engine.start() time",
    enabling graph reuse: Operon(graph, params={"x": None}) compiles once,
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


def graph(fn=None, *, bound: "str | None" = None, strict_dag: bool = False):
    """Decorator to turn a builder function into a reusable GraphOp factory.

    Can be used bare or with keyword arguments::

        @graph
        def pipeline(x):
            ...

        @graph(bound="io")
        def io_pipeline(x):
            ...

        @graph(strict_dag=True)
        def dag_only(x):
            # back-edges here FAIL validate() instead of being rewritten
            ...

    Args:
        bound: Execution bound for the graph. ``None`` auto-detects from children.
            ``"sync"`` forces inline dispatch, ``"io"``/``"cpu"`` forces task dispatch.
        strict_dag: Opt out of the Phase 3 cycle-to-loop rewrite. Back-edges
            in the graph body are left as-is (and hit the classic cycle
            warning path). Use when you want fail-fast behavior on accidental
            cycles.
    """

    def _make_graph_wrapper(fn, graph_bound, graph_strict_dag):
        from operonx.core.ops.graph.graph_op import GraphOp

        import inspect

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

            # Inject decorator-level bound if not overridden at call time
            if graph_bound is not None and "bound" not in init_kwargs:
                init_kwargs["bound"] = graph_bound
            # Inject decorator-level strict_dag if not overridden at call time
            if graph_strict_dag and "strict_dag" not in init_kwargs:
                init_kwargs["strict_dag"] = True

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
        return _make_graph_wrapper(fn, bound, strict_dag)
    # @graph(bound="io", strict_dag=True) with parentheses
    return lambda f: _make_graph_wrapper(f, bound, strict_dag)
