"""Auto-generate Pydantic request/response models from GraphOp metadata."""

from typing import Any, Dict, Type

from pydantic import BaseModel, Field, create_model

from hush.core.ops.graph.graph_op import GraphOp

_TYPE_MAP = {
    str: str,
    int: int,
    float: float,
    bool: bool,
    list: list,
    dict: dict,
}


def _to_pascal(name: str) -> str:
    """Convert snake_case or kebab-case to PascalCase."""
    return "".join(word.capitalize() for word in name.replace("-", "_").split("_"))


def _param_to_field(param) -> tuple:
    """Convert a Param to a (type, Field) tuple for create_model."""
    py_type = _TYPE_MAP.get(param.type, Any)
    if param.required:
        return (py_type, Field(..., description=param.description or ""))
    else:
        return (py_type, Field(default=param.default, description=param.description or ""))


def build_request_model(graph: GraphOp) -> Type[BaseModel]:
    """Build a Pydantic model from graph.inputs.

    Reads the graph's input Param definitions and creates a dynamic
    Pydantic model for request validation and OpenAPI documentation.
    """
    fields = {}
    for var_name, param in graph.inputs.items():
        if var_name.startswith("$"):
            continue
        fields[var_name] = _param_to_field(param)

    model_name = f"{_to_pascal(graph.name)}Request"
    if not fields:
        fields["placeholder_"] = (Any, Field(default=None, exclude=True))
    return create_model(model_name, **fields)


def build_response_model(graph: GraphOp) -> Type[BaseModel]:
    """Build a Pydantic model from graph.outputs."""
    fields = {}
    for var_name, param in graph.outputs.items():
        if var_name.startswith("$"):
            continue
        fields[var_name] = _param_to_field(param)

    model_name = f"{_to_pascal(graph.name)}Response"
    if not fields:
        fields = {"result": (Any, Field(default=None, description="Workflow result"))}
    return create_model(model_name, **fields)


def strip_internal_keys(d: dict) -> dict:
    """Remove keys prefixed with '$' from a result dict.

    Keys starting with '$' are internal Hush metadata (e.g. ``$tags``,
    ``$trace_id``) and should never be exposed to API callers.
    """
    return {k: v for k, v in d.items() if not k.startswith("$")}


def graph_schema_info(graph: GraphOp) -> Dict[str, Any]:
    """Return a JSON-serializable schema description for an endpoint."""
    inputs = {}
    for name, param in graph.inputs.items():
        if name.startswith("$"):
            continue
        inputs[name] = {
            "type": getattr(param.type, "__name__", str(param.type)),
            "required": param.required,
            "default": param.default,
            "description": param.description,
        }

    outputs = {}
    for name, param in graph.outputs.items():
        if name.startswith("$"):
            continue
        outputs[name] = {
            "type": getattr(param.type, "__name__", str(param.type)),
            "description": param.description,
        }

    return {"name": graph.name, "inputs": inputs, "outputs": outputs}
