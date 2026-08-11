"""``@tool`` — a tool is an ``@op`` that also carries LLM-facing metadata.

Two consumers need two different descriptions of the same function:

- **operonx** needs ``Param`` wiring, inferred from the signature by
  ``@op``, so the tool can be a node in a graph.
- **the model** needs a JSON Schema in the ``tools=[…]`` request payload.

They cannot be derived from one another — a JSON Schema carries prose
descriptions and enum constraints a Python signature does not, and
``Param`` carries graph wiring the model must never see. So ``@tool``
keeps them side by side rather than pretending one generates the other.

The decorator returns the **same op factory** ``@op`` would, with a
``_tool_meta`` dict attached. Dispatch reuses the op's own execution path
(tracing, timing, bound routing, cancellation) rather than calling the
raw function.

Example::

    @tool(
        name="read_file",
        description="Read a UTF-8 text file and return its contents.",
        schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        readonly=True,
    )
    async def read_file(path: str) -> dict:
        return {"content": Path(path).read_text()}
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional

from operonx.core.ops.transform.func_op import op

__all__ = ["TOOL_REGISTRY", "ToolMeta", "tool", "get_tool_definitions", "clear_registry"]

# name → op factory. Populated at import time by the decorator; dispatch
# resolves through this rather than closing over the function, so a tool
# defined in one module is callable from a graph built in another.
TOOL_REGISTRY: Dict[str, Any] = {}


class ToolMeta(dict):
    """Tool metadata, kept as a dict so it stays trivially serialisable.

    Attribute access is a convenience for reading; treat it as read-only
    once registered — dispatch reads these on every call.
    """

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as exc:  # pragma: no cover - attribute typo path
            raise AttributeError(item) from exc


def tool(
    *,
    name: str,
    description: str,
    schema: Dict[str, Any],
    readonly: bool = False,
    destructive: bool = False,
    concurrency_safe: bool = True,
    max_result_chars: int = 100_000,
    timeout: Optional[float] = None,
    **op_kwargs: Any,
) -> Callable[[Callable], Any]:
    """Register a function as both an ``@op`` and an LLM-callable tool.

    Args:
        name: Identifier the model uses in its tool call. Must be unique.
        description: Prose the model reads when deciding to call it.
        schema: JSON Schema for the arguments — the ``parameters`` object
            sent to the provider. Not derived from the signature; see the
            module docstring.
        readonly: Declares the tool has no side effects. Advisory metadata
            for policy layers; nothing enforces it.
        destructive: Routes the call through a human approval gate before
            execution. Anything that writes, deletes, spends, or sends.
        concurrency_safe: False pins the tool to sequential dispatch even
            when sibling calls fan out.
        max_result_chars: Result truncation budget. A tool that returns a
            10 MB file would otherwise blow the context window and the
            prompt cache in one call.
        timeout: Per-call wall-clock limit in seconds. ``None`` inherits
            whatever the caller imposes.
        **op_kwargs: Forwarded to ``@op`` — ``bound``, ``cache``,
            ``exclude``, ``include``, ``observe_max``.

    Returns:
        The op factory, with ``_tool_meta`` attached.

    Raises:
        ValueError: on a duplicate name, an empty description, or a
            schema that is not a JSON Schema object. All three are
            silent-failure modes at runtime: a duplicate name shadows a
            tool, an empty description makes the model guess, and a
            malformed schema is rejected by the provider with an error
            that names the request, not the tool.
    """
    if not name or not name.strip():
        raise ValueError("@tool needs a non-empty name — the model calls it by name.")
    if not description or not description.strip():
        raise ValueError(
            f"@tool '{name}' needs a description. It is the only thing the model "
            f"reads when deciding whether to call this tool."
        )
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError(
            f"@tool '{name}' schema must be a JSON Schema object "
            f'(``{{"type": "object", "properties": {{...}}}}``), got '
            f"{type(schema).__name__}"
            + (f" with type={schema.get('type')!r}" if isinstance(schema, dict) else "")
        )
    if name in TOOL_REGISTRY:
        raise ValueError(
            f"@tool '{name}' is already registered by "
            f"{getattr(TOOL_REGISTRY[name], '_tool_meta', {}).get('qualname', '?')}. "
            f"Tool names are the model's only handle on a tool; a duplicate "
            f"silently shadows one of them."
        )

    def wrap(fn: Callable) -> Any:
        meta = ToolMeta(
            name=name,
            description=description,
            schema=schema,
            readonly=readonly,
            destructive=destructive,
            concurrency_safe=concurrency_safe,
            max_result_chars=max_result_chars,
            timeout=timeout,
            qualname=f"{fn.__module__}.{fn.__qualname__}",
        )
        factory = op(**op_kwargs)(fn) if op_kwargs else op(fn)
        # The factory is what graphs instantiate; the metadata rides on it
        # so dispatch needs only the registry entry.
        factory._tool_meta = meta
        TOOL_REGISTRY[name] = factory
        return factory

    return wrap


def get_tool_definitions(names: Optional[Iterable[str]] = None) -> list[dict]:
    """Build the ``tools=[…]`` payload for an LLM request.

    Args:
        names: Restrict to these tools, in this order. ``None`` sends
            every registered tool. Passing an explicit subset is how a
            sub-agent gets a narrower toolset — enforce it here, at the
            construction site, since there is no capability token.

    Raises:
        KeyError: naming an unregistered tool. Silently dropping it would
            give the sub-agent a smaller toolset than its author intended
            and no indication why.
    """
    selected = list(TOOL_REGISTRY) if names is None else list(names)
    missing = [n for n in selected if n not in TOOL_REGISTRY]
    if missing:
        raise KeyError(
            f"unregistered tool(s): {missing}. Registered: {sorted(TOOL_REGISTRY)}. "
            f"Import the module that defines them — @tool registers at import time."
        )
    return [
        {
            "type": "function",
            "function": {
                "name": TOOL_REGISTRY[n]._tool_meta["name"],
                "description": TOOL_REGISTRY[n]._tool_meta["description"],
                "parameters": TOOL_REGISTRY[n]._tool_meta["schema"],
            },
        }
        for n in selected
    ]


def clear_registry() -> None:
    """Empty ``TOOL_REGISTRY``. For tests — the registry is process-wide,
    so a test that registers a tool leaks it into every later test."""
    TOOL_REGISTRY.clear()
