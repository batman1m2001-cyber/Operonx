"""Parameter resolution and normalization for op inputs/outputs.

Converts raw user-provided values (Refs, literals, op references, wildcards)
into Dict[str, Param] suitable for BaseOp.inputs / BaseOp.outputs.
"""

from typing import Any, Dict

from hush.core.states.ref import Ref
from hush.core.utils.common import Param


def resolve_value(key: str, value: Any, parent) -> Any:
    """Convert value to a Ref or keep as a literal.

    Supported formats:
        - some_op → Ref(some_op, key)
        - some_op["other"] → Ref(some_op, "other")
        - Ref(op, "var") → kept as-is
        - PARENT["x"] → Ref(parent, "x")
        - literal → kept as-is
    """

    def resolve_parent(source):
        """Resolve PARENT marker to the actual parent op."""
        if hasattr(source, "name") and source.name == "__PARENT__":
            return parent if parent else source
        return source

    # Handle Ref directly — keep transforms intact
    if isinstance(value, Ref):
        resolved = resolve_parent(value.raw_source)
        return Ref(resolved, value.var, value.transforms)

    # Handle op reference: some_op → Ref(some_op, key)
    if hasattr(value, "name"):
        resolved = resolve_parent(value)
        return Ref(resolved, key)

    # Literal value
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
