"""Shared helpers for the ops package.

Kept separate from base.py and _edges.py to avoid the circular import that
would arise if _edges.py imported helpers directly from base.py
(base.py in turn re-exports DummyOp/START/END/PARENT from _edges.py).
"""

from hush.core.states.ref import Ref


def _has_explicit_outputs(op) -> bool:
    """Check whether the op has user-defined explicit output mappings.

    Returns True if any output Param has a non-None value (user-set).
    Returns False if outputs are absent, empty, or all auto-parsed.
    """
    if not hasattr(op, "outputs") or op.outputs is None:
        return False
    if len(op.outputs) == 0:
        return False
    for param in op.outputs.values():
        if hasattr(param, "value") and param.value is not None:
            return True
    return False


def _set_wildcard_outputs(target_op) -> None:
    """Auto-set wildcard outputs for ops connecting to END.

    For each output key with no explicit value, sets it to Ref(parent, key)
    so the op's outputs are automatically forwarded to the parent graph.
    """
    if hasattr(target_op, "outputs") and not _has_explicit_outputs(target_op):
        if target_op.outputs is None:
            target_op.outputs = {}
        parent_op = getattr(target_op, "parent", None)
        if parent_op is None:
            return
        for key in target_op.outputs:
            param = target_op.outputs[key]
            if hasattr(param, "value") and param.value is None:
                param.value = Ref(parent_op, key)
