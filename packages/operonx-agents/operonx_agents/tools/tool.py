"""``@tool`` — a function the model can call, typed from its signature.

The JSON Schema the model sees and the validation its arguments go
through come from one source: a pydantic model built over the function's
parameters. Argument descriptions come from the docstring (Google, NumPy
or Sphinx style)::

    @tool
    async def lookup_order(ctx: RunContext[Deps], order_id: str) -> Order:
        \"\"\"Look up an order by its code.

        Args:
            order_id: The 8-character order code the customer read out.
        \"\"\"
        return await ctx.deps.crm.order(order_id)

    @tool(readonly=True, timeout=5)
    def forecast(city: str, days: Annotated[int, Field(ge=1, le=7)] = 3) -> dict: ...

A first parameter annotated ``RunContext`` receives the run's context and
is not part of the schema. ``schema=`` overrides the generated schema for
the odd case a signature cannot say (arguments are then validated against
the signature still).

There is no registry. A tool belongs to the :class:`Toolset` (one per
agent) it is put in, and only that toolset's dispatch can run it — the
process-wide registry of ``operonx.agents`` let any agent run any tool a
model named.
"""

from __future__ import annotations

import inspect
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal, Optional, Union

import pydantic
from pydantic import BaseModel, ConfigDict, create_model

from operonx_agents.errors import ToolDefinitionError
from operonx_agents.run.context import RunContext
from operonx_agents.tools._docstring import parse_docstring

__all__ = ["Tool", "ToolSpec", "tool"]

#: Who decides whether a call runs without a human: never asked, always
#: asked, or a function of the context and the validated arguments.
Approval = Union[Literal["never", "always"], Callable[[RunContext, Dict[str, Any]], bool]]


@dataclass(frozen=True)
class ToolSpec:
    """What the model is told about a tool, and how dispatch treats it.

    Attributes:
        name: What the model calls it.
        description: What the model reads to decide when to call it.
        params_schema: The JSON Schema of its arguments.
        approval: ``"never"`` (default), ``"always"``, or
            ``(ctx, args) -> bool``: whether this call needs a human.
        idempotent: Running it twice is harmless. A run resumed after a
            crash re-runs an idempotent call that was in flight and
            answers any other one "outcome unknown".
        sequential: Runs alone, in the order the model emitted it, after
            the concurrent calls of its turn. Default: ``not readonly``.
        timeout: Seconds one call may take; past it the model is told it
            timed out. An ``async def`` tool only: a plain ``def`` runs on
            the event loop, where nothing can interrupt it.
        max_result_chars: The result is cut at this length, with a note
            saying so — silent truncation makes a model reason about half
            a file as if it were whole.
        readonly: It changes nothing. Read by the policy and by MCP.
        destructive: It deletes, spends or sends. Read by the policy.
    """

    name: str
    description: str
    params_schema: Dict[str, Any]
    approval: Approval = "never"
    idempotent: bool = True
    sequential: bool = True
    timeout: Optional[float] = None
    max_result_chars: int = 100_000
    readonly: bool = False
    destructive: bool = False

    def definition(self) -> Dict[str, Any]:
        """The tool as a Chat Completions ``tools=[...]`` entry (the
        Anthropic backend converts it)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.params_schema,
            },
        }


@dataclass(frozen=True)
class Tool:
    """A function plus its :class:`ToolSpec`. Calling the tool calls the
    function, so it stays testable as itself."""

    spec: ToolSpec
    function: Callable[..., Any]
    takes_context: bool
    args_model: type = field(repr=False)

    @property
    def name(self) -> str:
        return self.spec.name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.function(*args, **kwargs)

    def validate(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """The arguments as the function takes them, or
        ``pydantic.ValidationError`` naming each bad field.

        Nested models arrive as model instances, not dicts: what the
        signature says.
        """
        model = self.args_model.model_validate(args)
        return {name: getattr(model, name) for name in type(model).model_fields}


def tool(
    function: Optional[Callable[..., Any]] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    schema: Optional[Dict[str, Any]] = None,
    approval: Approval = "never",
    idempotent: bool = True,
    sequential: Optional[bool] = None,
    timeout: Optional[float] = None,
    max_result_chars: int = 100_000,
    readonly: bool = False,
    destructive: bool = False,
) -> Any:
    """Make a function a :class:`Tool`. Use bare (``@tool``) or with
    arguments (``@tool(readonly=True)``); they are :class:`ToolSpec`'s.

    ``name`` defaults to the function's name and ``description`` to its
    docstring summary. ``sequential`` defaults to ``not readonly``: only a
    tool that changes nothing runs concurrently with its siblings unless
    it says otherwise.

    Raises:
        ToolDefinitionError: for a signature the model cannot call —
            ``*args``/``**kwargs``, a ``RunContext`` that is not the first
            parameter, an approval that is not ``"never"``/``"always"``/a
            function, or a tool with no description at all.
    """

    def build(fn: Callable[..., Any]) -> Tool:
        return _build(
            fn,
            name=name,
            description=description,
            schema=schema,
            approval=approval,
            idempotent=idempotent,
            sequential=(not readonly) if sequential is None else sequential,
            timeout=timeout,
            max_result_chars=max_result_chars,
            readonly=readonly,
            destructive=destructive,
        )

    return build(function) if function is not None else build


def _is_context(annotation: Any) -> bool:
    return annotation is RunContext or typing.get_origin(annotation) is RunContext


def _build(fn: Callable[..., Any], *, name, description, schema, approval, **spec_kwargs) -> Tool:
    tool_name = name or fn.__name__
    if not (approval in ("never", "always") or callable(approval)):
        raise ToolDefinitionError(
            f"tool {tool_name!r}: approval={approval!r} is not 'never', 'always' or a function "
            "(ctx, args) -> bool. A typo here must not quietly mean 'never ask'."
        )
    summary, arg_docs = parse_docstring(inspect.getdoc(fn))
    text = description if description is not None else summary
    if not text:
        raise ToolDefinitionError(
            f"tool {tool_name!r} has no description: the model decides when to call a tool "
            "from it. Give the function a docstring, or pass @tool(description=...)."
        )
    try:
        hints = typing.get_type_hints(fn, include_extras=True)
    except Exception as exc:  # an annotation that names something undefined
        raise ToolDefinitionError(
            f"tool {tool_name!r}: its annotations do not resolve ({exc}). Import every type "
            "the signature names at module level."
        ) from exc

    fields: Dict[str, Any] = {}
    takes_context = False
    for index, param in enumerate(inspect.signature(fn).parameters.values()):
        annotation = hints.get(param.name, Any)
        if _is_context(annotation):
            if index != 0:
                raise ToolDefinitionError(
                    f"tool {tool_name!r}: the RunContext parameter {param.name!r} must come "
                    "first, so the model's arguments are everything after it."
                )
            takes_context = True
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise ToolDefinitionError(
                f"tool {tool_name!r}: *{param.name} cannot be described to a model. Declare "
                "each argument by name."
            )
        default = ... if param.default is inspect.Parameter.empty else param.default
        doc = arg_docs.get(param.name)
        fields[param.name] = (
            annotation,
            pydantic.Field(default, description=doc) if doc else default,
        )

    args_model = create_model(
        f"{tool_name}_args",
        __config__=ConfigDict(extra="forbid", arbitrary_types_allowed=True),
        **fields,
    )
    params_schema = schema if schema is not None else model_schema(args_model)
    return Tool(
        spec=ToolSpec(
            name=tool_name,
            description=text,
            params_schema=params_schema,
            approval=approval,
            **spec_kwargs,
        ),
        function=fn,
        takes_context=takes_context,
        args_model=args_model,
    )


def model_schema(model: type[BaseModel]) -> Dict[str, Any]:
    """A model's JSON Schema as a model gateway takes it: ``$ref``\\ s inlined
    (several gateways reject or ignore ``$defs``), pydantic's ``title``\\ s
    dropped (noise in every request), and the top level an object.

    A self-referencing model keeps its ``$defs``: it cannot be inlined.
    """
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def inline(node: Any, seen: frozenset, refs: bool = True) -> Any:
        """``node`` is a schema; a ``properties`` map is walked by name.
        ``refs=False`` only drops titles."""
        if isinstance(node, list):
            return [inline(v, seen, refs) for v in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if refs and isinstance(ref, str) and ref.startswith("#/$defs/"):
            key = ref.split("/")[-1]
            if key in seen:
                raise _Recursive
            extra = {k: v for k, v in node.items() if k != "$ref"}
            return {**inline(defs[key], seen | {key}), **inline(extra, seen)}
        out = {}
        for k, v in node.items():
            if k == "title":
                continue
            if k in ("properties", "patternProperties", "$defs") and isinstance(v, dict):
                out[k] = {name: inline(sub, seen, refs) for name, sub in v.items()}
            else:
                out[k] = inline(v, seen, refs)
        return out

    try:
        out = inline(raw, frozenset())
    except _Recursive:
        out = inline({**raw, "$defs": defs}, frozenset(), refs=False)
    out.setdefault("properties", {})
    out["type"] = "object"
    return out


class _Recursive(Exception):
    pass
