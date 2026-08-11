"""Parameter resolution and normalization for op inputs/outputs.

Converts raw user-provided values (Refs, literals, op references, wildcards)
into Dict[str, Param] suitable for BaseOp.inputs / BaseOp.outputs.
"""

from typing import Any, Dict

from operonx.core.states.ref import Ref
from operonx.core.utils.common import Param


def _find_nested_ref(
    value: Any, path: str = "", _seen: set[int] | None = None
) -> tuple[str, Ref] | None:
    """Return ``(path, ref)`` for the first Ref buried in a container.

    A Ref is only wired when it is *the* value of a param — one param maps
    to one state cell, which holds one pull-ref. A Ref nested inside a
    dict or list has no cell to live in, so it silently stays a ``Ref``
    object: the op receives it verbatim, no dependency edge is created,
    and the cross-graph scope check in ``GraphOp._validate`` never sees
    it. Callers use this to reject the syntax at construction time
    instead of letting it fail as wrong data at runtime.

    The walk is exhaustive rather than depth-capped. A cap would make the
    original bug reappear one level past it — silently, which is the
    whole failure mode being fixed here. Cost is one pass over a
    hand-written literal at construction time; self-referential
    structures terminate via ``_seen``.
    """
    if _seen is None:
        _seen = set()

    # A Ref carries transforms (`s["a"] + 1`, `s["a"] >= 5`) and stream
    # policy (`.parallel()`, `.collect()`) but is still a Ref, so it is
    # checked before the container branches — every operator in ref.py
    # returns a new Ref rather than some other type.
    if isinstance(value, Ref):
        return path, value

    if not isinstance(value, (dict, list, tuple)):
        return None

    marker = id(value)
    if marker in _seen:
        return None
    _seen.add(marker)

    # Dict *keys* and set members need no handling: Ref overloads __eq__
    # to build branch conditions and so is unhashable — Python rejects
    # `{ref: v}` and `{ref}` before we ever see them.
    if isinstance(value, dict):
        items = ((f"{path}[{k!r}]", v) for k, v in value.items())
    else:
        items = ((f"{path}[{i}]", v) for i, v in enumerate(value))

    for sub_path, v in items:
        found = _find_nested_ref(v, sub_path, _seen)
        if found:
            return found
    return None


def _reject_nested_ref(key: str, value: Any) -> None:
    """Raise if ``value`` is a container hiding a Ref. No-op otherwise.

    Top-level Refs are handled by :func:`resolve_value` and never reach
    here as containers, so this only fires on the unwired form.
    """
    found = _find_nested_ref(value)
    if found is None:
        return

    path, ref = found
    source = getattr(ref.raw_source, "name", None) or repr(ref.raw_source)
    raise TypeError(
        f"Input '{key}' has a Ref nested inside a {type(value).__name__} "
        f"at '{key}{path}' → {source}.{ref.var}.\n"
        f"\n"
        f"Operonx wires one param to one state cell holding one ref, so a "
        f"Ref inside a container is never resolved: the op would receive "
        f"the Ref object itself, no dependency edge would be created, and "
        f"cross-graph scope checks would not see it.\n"
        f"\n"
        f"Pass it as its own param:\n"
        f"    my_op({key}_part=<source>['{ref.var}'], ...)\n"
        f"or assemble the container in an upstream @op and pass one ref:\n"
        f"    @op\n"
        f"    def build_{key}(part): return {{'{key}': {{...part...}}}}\n"
        f"    my_op({key}=build_{key}(part=<source>['{ref.var}'])['{key}'])"
    )


def resolve_value(key: str, value: Any, parent) -> Any:
    """Convert value to a Ref or keep as a literal.

    Supported formats:
        - some_op → Ref(some_op, key)
        - some_op["other"] → Ref(some_op, "other")
        - Ref(op, "var") → kept as-is
        - PARENT["x"] → Ref(parent, "x")
        - literal → kept as-is

    Raises:
        TypeError: if ``value`` is a container with a Ref buried in it —
            a form operonx cannot wire, and which used to degrade to a
            literal silently. See :func:`_reject_nested_ref`.
    """

    def resolve_parent(source):
        """Resolve PARENT marker to the actual parent op."""
        if hasattr(source, "name") and source.name == "__PARENT__":
            return parent if parent else source
        return source

    # Handle Ref directly — keep transforms + streaming attrs intact
    if isinstance(value, Ref):
        resolved = resolve_parent(value.raw_source)
        new_ref = Ref(resolved, value.var, value.transforms)
        # Preserve streaming modifiers (.parallel(), .collect())
        object.__setattr__(new_ref, "_stream_parallel", value._stream_parallel)
        object.__setattr__(new_ref, "_stream_parallel_max", value._stream_parallel_max)
        object.__setattr__(new_ref, "_stream_collect", value._stream_collect)
        return new_ref

    # Handle op reference: some_op → Ref(some_op, key)
    if hasattr(value, "name"):
        resolved = resolve_parent(value)
        return Ref(resolved, key)

    # Literal value — but a "literal" holding a Ref is the silent-failure
    # case, not a literal. Reject it here, where we still know the param
    # name, rather than at runtime where it surfaces as wrong data.
    _reject_nested_ref(key, value)
    return value


def normalize_params(params: Any, parent) -> Dict[str, Param]:
    """Normalize inputs/outputs to Dict[str, Param].

    Supported formats:
        - params=None → {}
        - params={"*": PARENT} → forward all keys from PARENT
        - params={"x": 1, "*": PARENT} → x=1, rest from PARENT
        - params={"var": Param(...)} → kept as-is, resolve value
        - params={"var": some_op} → {"var": Param(value=Ref(some_op, "var"))}
        - params={"var": some_op["other"]} → {"var": Param(value=Ref(some_op, "other"))}
        - params={"var": literal} → {"var": Param(value=literal)}
        - params={("a", "b"): some_op} → expanded to both keys
    """
    if params is None:
        return {}

    result = {}

    if isinstance(params, dict):
        for key, value in params.items():
            # Handle wildcard "*" key - store for later processing in merge_params
            if key == "*":
                # Validate that value is an op reference (PARENT or another op)
                if hasattr(value, "name"):
                    result["__FORWARD_WILDCARD__"] = value
                else:
                    raise ValueError(
                        f"Wildcard '*' key must have an op reference as value (e.g. PARENT), "
                        f"got: {type(value)}"
                    )
                continue

            # Handle tuple keys: {("a", "b"): op} → expand to both
            if isinstance(key, tuple):
                for k in key:
                    resolved_value = resolve_value(k, value, parent)
                    result[k] = Param(value=resolved_value)
            # Handle Param directly
            elif isinstance(value, Param):
                # Resolve value inside Param if present
                if value.value is not None:
                    value.value = resolve_value(key, value.value, parent)
                result[key] = value
            else:
                # Create new Param with resolved value (type auto-inferred)
                resolved_value = resolve_value(key, value, parent)
                result[key] = Param(value=resolved_value)

    return result


def merge_params(
    schema: Dict[str, Param], user_provided: Dict[str, Any], parent
) -> Dict[str, Param]:
    """Merge schema (from parsing) with user-provided inputs/outputs.

    - If key already exists in schema → assign value only
    - If key is new → create new Param (type auto-inferred)
    - If user_provided has __FORWARD_WILDCARD__ marker → forward keys
      not explicitly defined to the op reference (PARENT)

    Args:
        schema: Dict[str, Param] from parsing (e.g. from function signature).
        user_provided: Dict from user (Ref | literal | Param).
        parent: The parent op for resolving PARENT refs.

    Returns:
        Merged Dict[str, Param].
    """
    # Copy schema to avoid mutating original
    result = {
        k: Param(
            type=v.type,
            required=v.required,
            default=v.default,
            description=v.description,
            value=v.value,
        )
        for k, v in schema.items()
    }

    # Resolve PARENT refs in schema values
    for key, param in result.items():
        if isinstance(param.value, Ref):
            param.value = resolve_value(key, param.value, parent)

    if not user_provided:
        return result

    # Make a copy to avoid mutating the original dict
    user_provided = dict(user_provided)

    # Extract wildcard source if present
    wildcard_source = user_provided.pop("__FORWARD_WILDCARD__", None)

    # Process explicitly provided keys
    explicitly_set = set()
    for key, value in user_provided.items():
        keys = key if isinstance(key, tuple) else (key,)
        for k in keys:
            explicitly_set.add(k)
            if k in result:
                if isinstance(value, Param):
                    result[k].value = (
                        resolve_value(k, value.value, parent) if value.value is not None else None
                    )
                else:
                    result[k].value = resolve_value(k, value, parent)
            elif isinstance(value, Param):
                if value.value is not None:
                    value.value = resolve_value(k, value.value, parent)
                result[k] = value
            else:
                result[k] = Param(value=resolve_value(k, value, parent))

    # Apply wildcard forwarding for remaining keys
    if wildcard_source is not None:
        if hasattr(wildcard_source, "name") and wildcard_source.name == "__PARENT__":
            resolved_source = parent if parent else wildcard_source
        else:
            resolved_source = wildcard_source
        for key in result:
            if key not in explicitly_set:
                result[key].value = Ref(resolved_source, key)

    return result
