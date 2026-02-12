"""Auto-naming: extract variable names from calling code for Hush nodes.

When a node is created without an explicit ``name``, this module inspects
the calling frame to extract the variable name from the assignment statement.

Strategy: bytecode analysis (primary) → source parsing (fallback) → None.

Example::

    llm = LLMNode.of(resource_key="gpt-4o")
    # llm.name == "llm" — auto-detected from the assignment

Public API:
    - ``auto_name()`` — extract variable name from calling assignment
    - ``unique_name()`` — generate 8-char hex UUID fallback name
    - ``register_skip(fn)`` — register a function for frame skipping
"""

import ast
import dis
import inspect
import linecache
import re
import uuid
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
    3. Frames with local variable ``_skip_auto_name = True`` (backward compat)

    Then tries bytecode analysis first (no source needed, handles multi-line),
    falling back to AST source parsing.

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
        # Primary: bytecode analysis
        name = _name_from_bytecode(frame)
        if name is not None:
            return name
        # Fallback: source code parsing
        return _name_from_source(frame.f_code.co_filename, frame.f_lineno)
    finally:
        del frame


# ── Frame Walking ──────────────────────────────────────────────────


def _should_skip(frame) -> bool:
    """Check if a frame should be skipped during the walk."""
    # Skip all __init__ methods (constructor chain)
    if frame.f_code.co_name == "__init__":
        return True
    # Skip registered code objects (shorthand .of(), @code_node wrapper, etc.)
    if frame.f_code in _skip_code_objects:
        return True
    # Backward compat: skip frames with the legacy local variable marker
    if frame.f_locals.get("_skip_auto_name"):
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
    try:
        instructions = list(dis.get_instructions(frame.f_code))
    except TypeError:
        return None

    offset = frame.f_lasti

    # Find the first instruction AFTER the call site
    i = 0
    while i < len(instructions) and instructions[i].offset <= offset:
        i += 1

    # Look at the next few instructions (small window)
    for j in range(i, min(i + 4, len(instructions))):
        opname = instructions[j].opname
        if opname in _STORE_OPS:
            return instructions[j].argval
        if opname in _BENIGN_OPS:
            continue
        break  # non-trivial instruction → not a simple assignment

    return None


# ── Source Parsing (Fallback) ──────────────────────────────────────


def _name_from_source(filename: str, lineno: int) -> Optional[str]:
    """Try to resolve a variable name from source code around the given line.

    Searches up to 6 lines above the current line to handle
    multi-line expressions where the assignment target is above.
    """
    for offset in range(6):
        line = linecache.getline(filename, lineno - offset)
        if not line.strip():
            continue
        name = _parse_assignment(line.strip())
        if name is not None:
            return name
    return None


def _parse_assignment(line: str) -> Optional[str]:
    """Parse a variable name from a single source line.

    Handles:
        - Simple assignment: ``name = expr``
        - Annotated assignment: ``name: Type = expr``
        - Multi-line (SyntaxError fallback): ``name = (`` via regex

    Rejects:
        - Comparisons: ``==``, ``>=``, ``!=``
        - Tuple unpack, augmented assignments, attribute/subscript targets
    """
    try:
        tree = ast.parse(line)
        if not tree.body:
            return None
        stmt = tree.body[0]
        # Simple assignment: name = ...
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
        ):
            return stmt.targets[0].id
        # Annotated assignment: name: Type = ...
        if (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.value is not None
        ):
            return stmt.target.id
    except SyntaxError:
        # Multi-line assignment: "var = (" can't be parsed alone.
        # Use regex to match "name = " (but not "==", ">=", "!=").
        m = re.match(r"(\w+)\s*=(?!=)", line)
        if m and m.group(1).isidentifier():
            return m.group(1)
    return None
