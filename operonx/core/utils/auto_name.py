"""Auto-naming: extract variable names from calling code for Operon nodes.

When a node is created without an explicit ``name``, this module inspects
the calling frame to extract the variable name from the assignment statement.

Strategy: the bytecode right after the call — a ``STORE`` of its result —
or ``None``. There is no source-line guess: the one there was read the
lines *above* the call and named a run after whatever was assigned there
(``params``, ``engine``, ``out``). With ``None`` the caller falls back to
a name it knows, such as a ``@graph`` function's own name.

Example::

    llm = LLMOp.of(resource="gpt-4o")
    # llm.name == "llm" — auto-detected from the assignment

Public API:
    - ``auto_name()`` — extract variable name from calling assignment
    - ``unique_name()`` — generate 8-char hex UUID fallback name
    - ``register_skip(fn)`` — register a function for frame skipping
"""

import dis
import inspect
import uuid
from functools import lru_cache
from types import CodeType
from typing import Optional, Set

# ── Code Object Registry ──────────────────────────────────────────

_skip_code_objects: Set[CodeType] = set()


def register_skip(fn):
    """Register a callable whose frame should be skipped during auto-naming.

    Use as a decorator or direct call::

        @register_skip
        def my_factory(**kwargs):
            return SomeNode(**kwargs)

        # or after definition:
        register_skip(wrapper_fn)

    Args:
        fn: A callable whose ``__code__`` will be recorded for skipping.

    Returns:
        The original function, unmodified.
    """
    _skip_code_objects.add(fn.__code__)
    return fn


# ── Public API ─────────────────────────────────────────────────────


def unique_name() -> str:
    """Generate a unique name using a UUID4 hex prefix (8 chars)."""
    return uuid.uuid4().hex[:8]


def auto_name() -> Optional[str]:
    """Extract variable name from the calling assignment statement.

    Walks up the call stack, skipping frames that belong to:

    1. ``__init__`` methods (constructor chain)
    2. Functions registered via ``register_skip()``

    Then reads the bytecode after the call: a ``STORE`` of its result is
    the name (``llm = LLMOp(...)`` → ``"llm"``). Anything else — an
    attribute or subscript target, a call chained onto the result
    (``out = await Operon(flow).run()``), no assignment at all — is
    ``None``.

    Returns:
        The variable name if found, or ``None``.
    """
    frame = inspect.currentframe()
    try:
        frame = frame.f_back  # skip this function
        while frame and _should_skip(frame):
            frame = frame.f_back
        if frame is None:
            return None
        return _name_from_bytecode(frame)
    finally:
        del frame


# ── Frame Walking ──────────────────────────────────────────────────


def _should_skip(frame) -> bool:
    """Check if a frame should be skipped during the walk."""
    # Skip all __init__ methods (constructor chain)
    if frame.f_code.co_name == "__init__":
        return True
    # Skip registered code objects (shorthand .of(), @op wrapper, etc.)
    if frame.f_code in _skip_code_objects:
        return True
    return False


# ── Bytecode Analysis (Primary) ───────────────────────────────────

_STORE_OPS = frozenset({"STORE_NAME", "STORE_FAST", "STORE_DEREF", "STORE_GLOBAL"})
_BENIGN_OPS = frozenset({"DUP_TOP", "NOP", "RESUME", "COPY", "CACHE"})


def _name_from_bytecode(frame) -> Optional[str]:
    """Extract variable name from the bytecode instruction after the call site.

    After a CALL instruction, the next meaningful instruction is typically
    ``STORE_FAST``/``STORE_NAME`` if the result is assigned to a simple variable.
    """
    return _name_at(frame.f_code, frame.f_lasti)


@lru_cache(maxsize=4096)
def _name_at(code: CodeType, offset: int) -> Optional[str]:
    """The name stored right after the call at ``offset`` in ``code``.

    Memoised: a code object never changes, so neither does the answer for
    one call site — and disassembling the caller's whole function on every
    op built there cost ~1 ms in a large one (an agent's tool dispatch built
    an op per tool call). Bounded, so code compiled at run time (``exec``
    in a loop) cannot grow it without limit.
    """
    try:
        instructions = list(dis.get_instructions(code))
    except TypeError:
        return None

    # Find the first instruction AFTER the call site
    i = 0
    while i < len(instructions) and instructions[i].offset <= offset:
        i += 1

    # Look at the next few instructions (small window)
    copied = False
    for j in range(i, min(i + 4, len(instructions))):
        opname = instructions[j].opname
        if opname in _STORE_OPS:
            # Two stores in a row with no copy before them is a tuple unpack
            # (`a, b = f(), g()`): on 3.11+ the last call is followed by the
            # last target's store, so only it would be named — on 3.10 a
            # ROT_TWO comes first and neither is. Name neither, everywhere.
            # `x = y = f()` copies first, and keeps its name.
            after = instructions[j + 1].opname if j + 1 < len(instructions) else ""
            if after in _STORE_OPS and not copied:
                return None
            return instructions[j].argval
        if opname in _BENIGN_OPS:
            copied = copied or opname in ("COPY", "DUP_TOP")
            continue
        break  # non-trivial instruction → not a simple assignment

    return None
