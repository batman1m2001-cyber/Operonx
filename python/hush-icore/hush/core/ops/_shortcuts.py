"""Shorthand utilities for op creation.

Provides the base init keys, kwargs splitter, and @shorthand decorator
used by Op.of() classmethods and the @graph decorator.
"""

from hush.core.loggings import LOGGER
from hush.core.states.ref import Ref
from hush.core.utils.auto_name import register_skip

# Base init keys shared by ALL nodes (from BaseOp.__init__)
_BASE_INIT_KEYS = frozenset(
    {
        "name",
        "id",
        "description",
        "inputs",
        "outputs",
        "sources",
        "targets",
        "stream",
        "start",
        "executor",
        "bound",
        "contain_generation",
    }
)


def split_shorthand_kwargs(kwargs: dict, extra_init_keys: set = None) -> tuple:
    """Split flat kwargs into (inputs, init_kwargs).

    Used by shorthand functions (llm_, for_, op, etc.) to separate
    op constructor kwargs from input mappings.

    Args:
        kwargs: Flat keyword arguments from shorthand function.
        extra_init_keys: Additional op-specific init keys beyond base keys
            (e.g., {'max_concurrency', 'callback'} for iteration ops).

    Returns:
        (inputs, init_kwargs) tuple where:
        - inputs: Dict of input variable mappings
        - init_kwargs: Dict of op constructor arguments

    Example:
        # Provider ops - just base keys
        inputs, init_kwargs = split_shorthand_kwargs(kwargs)

        # Iteration ops - with extra keys
        inputs, init_kwargs = split_shorthand_kwargs(
            kwargs,
            {'max_concurrency', 'until', 'callback'}
        )
    """
    init_keys = _BASE_INIT_KEYS | (extra_init_keys or set())

    inputs = {}
    init_kwargs = {}

    for key, value in kwargs.items():
        if key in init_keys and not isinstance(value, Ref):
            init_kwargs[key] = value
        else:
            if key in init_keys:
                LOGGER.warning(
                    "Keyword '%s' is a reserved op parameter (one of %s) but received a Ref value. "
                    "It will be treated as an input mapping instead of an op constructor arg. "
                    "Consider renaming this parameter to avoid ambiguity.",
                    key,
                    sorted(init_keys),
                )
            inputs[key] = value

    return inputs, init_kwargs


def shorthand(fn):
    """Decorator for ``Op.of()`` classmethods.

    Registers the function for auto-naming frame skip via ``register_skip()``
    and wraps as ``classmethod``.

    Usage::

        class MyOp(BaseOp):
            @shorthand
            def of(cls, my_param=None, **kwargs):
                inputs, init_kwargs = split_shorthand_kwargs(kwargs)
                return cls(my_param=my_param, inputs=inputs or None, **init_kwargs)
    """
    register_skip(fn)
    return classmethod(fn)
