"""@graph and @graph.loop decorators for building reusable GraphOp factories."""

import inspect
from functools import wraps

from hush.core.loggings import LOGGER
from hush.core.ops._shortcuts import _BASE_INIT_KEYS, split_shorthand_kwargs
from hush.core.ops.base import PARENT
from hush.core.states.ref import Ref
from hush.core.utils.auto_name import register_skip


def _build_fn_args(input_mappings, param_names):
    """Build function arguments from input mappings.

    Ref values and bare PARENT become PARENT[key] refs (resolved at runtime
    from graph inputs). Static values are passed through as-is for use at
    graph build time.
    """
    args = {}
    for key, value in input_mappings.items():
        if key not in param_names:
            continue
        if isinstance(value, Ref) or value is PARENT:
            args[key] = PARENT[key]
        else:
            args[key] = value
    return args


def graph(fn):
    """Decorator to turn a builder function into a reusable GraphOp factory.

    The function's parameters become graph inputs. Ref values are injected as
    PARENT refs; static values are passed through directly for use at build time.
    Auto-naming works through the decorator (via register_skip).

    Example::

        @graph
        def verify_card(conversation):
            check = detect_card(conversation=conversation)
            START >> check >> END

        g1 = verify_card(conversation=other_op["conv"])
        # g1.name == "g1"

        # Static values pass through directly:
        @graph
        def my_chain(template, resource, query):
            p = PromptOp.of(template=template, query=query)
            llm = LLMOp.of(resource=resource, messages=p["messages"])
            START >> p >> llm >> END

        c = my_chain(
            template={"system": "You are helpful.", "user": "{query}"},
            resource="gpt-4o",       # static — passed directly
            query=PARENT["query"],   # Ref — becomes PARENT["query"]
        )
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
            fn_args = _build_fn_args(input_mappings, param_names)
            fn(**fn_args)
        return g

    register_skip(wrapper)
    wrapper.__wrapped__ = fn
    return wrapper


def _graph_loop(until=None, max_iterations=100):
    """Decorator factory for feedback-loop graphs.

    max_iterations can be an int or a string expression evaluated against kwargs::

        @graph.loop(until="error == None", max_iterations="retry + 1")
        def extract(error, retry=0, ...):
            ...

        e = extract(error="init", retry=2)  # max_iterations = 3

    Keyword-only params (after ``*``) are config — passed directly to fn,
    NOT stored as loop state. Regular params are loop state variables::

        @graph.loop(until="error == None", max_iterations="retry + 1")
        def extract(error, retry=0, *, resource=None, fields=None):
            # error, retry → loop state (PARENT refs, updated each iteration)
            # resource, fields → config (direct values, fixed)
            ...

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
        loop_params = set()
        config_params = set()
        for name, param in sig.parameters.items():
            if param.kind == inspect.Parameter.KEYWORD_ONLY:
                config_params.add(name)
            elif param.kind != inspect.Parameter.VAR_KEYWORD:
                loop_params.add(name)

        @wraps(fn)
        def wrapper(**kwargs):
            input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)

            # Split kwargs: loop state vs config (keyword-only params)
            # split_shorthand_kwargs puts unknown keys into input_mappings,
            # so loop params and config params may end up there too.
            loop_state = {}
            config = {}
            remaining_inputs = {}
            for k, v in init_kwargs.items():
                if k in config_params:
                    config[k] = v
                else:
                    loop_state[k] = v
            for k, v in (input_mappings or {}).items():
                if k in config_params:
                    config[k] = v
                elif k in loop_params:
                    loop_state[k] = v
                else:
                    remaining_inputs[k] = v
            input_mappings = remaining_inputs

            # Resolve max_iterations: string → eval against all kwargs + fn defaults
            if isinstance(max_iterations, str):
                # Include fn param defaults so expression can reference them
                defaults = {
                    name: p.default
                    for name, p in sig.parameters.items()
                    if p.default is not inspect.Parameter.empty
                }
                _max = eval(max_iterations, {}, {**defaults, **loop_state, **config})  # noqa: S307
            else:
                _max = max_iterations

            g = GraphOp.loop(until=until, max_iterations=_max, **loop_state)
            g.inputs.update(input_mappings or {})
            with g:
                # Loop state → PARENT refs, config → direct values
                parent_refs = {k: PARENT[k] for k in loop_state if k in loop_params}
                fn(**parent_refs, **config)
            return g

        register_skip(wrapper)
        wrapper.__wrapped__ = fn
        return wrapper

    return decorator


graph.loop = _graph_loop
