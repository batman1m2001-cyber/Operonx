"""chat() and extract() — LLM workflow building blocks.

- chat(): text generation (Prompt → LLM)
- extract(): structured output (Prompt → LLM → Parser) with retry + validation
"""

from typing import Any, Dict, List, Optional, Union

from hush.core.ops import END, PARENT, START, ParserOp, graph
from hush.core.ops.graph.graph_op import GraphOp
from hush.providers.ops.llm import LLMOp
from hush.providers.ops.prompt import PromptOp

# =============================================================================
# chat() — text generation
# =============================================================================


@graph
def chat(
    template: Any = None,
    resource: Optional[Union[str, List[str]]] = None,
    ratios: Optional[List[float]] = None,
    fallback: Optional[List[str]] = None,
    response_format: Optional[Dict[str, Any]] = None,
    delay: float = 0,
) -> Any:
    """Prompt → LLM graph for text generation.

    Returns raw LLM output: content, role, model_used, tokens_used, etc.

    Example::

        c = chat(
            resource="claude-haiku",
            template={"system": "You are helpful.", "user": "{query}"},
            query=PARENT["query"],
        )
    """
    _prompt = PromptOp(name="prompt", inputs={"*": PARENT})

    llm_inputs: Dict[str, Any] = {"messages": _prompt["messages"]}
    if response_format:
        llm_inputs["response_format"] = response_format

    _llm = LLMOp(
        name="llm",
        resource=resource,
        ratios=ratios,
        fallback=fallback,
        inputs=llm_inputs,
        outputs={"*": PARENT},
        delay=delay,
    )

    START >> _prompt >> _llm >> END


# =============================================================================
# extract() — structured output with retry + validation
# =============================================================================


def extract(
    template: Any = None,
    resource: Optional[Union[str, List[str]]] = None,
    ratios: Optional[List[float]] = None,
    fallback: Optional[List[str]] = None,
    fields: Optional[List[str]] = None,
    parser: str = "xml",
    response_format: Optional[Dict[str, Any]] = None,
    delay: float = 0,
    retry: int = 0,
    validators: Optional[Dict[str, list]] = None,
    default: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> GraphOp:
    """Prompt → LLM → Parser with retry via GraphOp.loop.

    Parses LLM output into typed fields. Retries on parse/validation failure.
    When all retries exhausted, keeps default values.

    Args:
        fields: Output fields to extract (e.g. ["intent: str", "confidence: float"]).
        parser: Parser format ("xml", "json", "yaml").
        retry: Number of retries on parse/validation failure (0 = no retry).
        validators: Allowed values per field (e.g. {"intent": ["CONFIRM", "DENY"]}).
        default: Default values when all retries exhausted.

    Example::

        e = extract(
            resource="claude-haiku",
            template="Classify: {speech}",
            fields=["result: str"],
            parser="xml",
            retry=2,
            validators={"result": ["CONFIRM", "DENY", "FALLBACK"]},
            default={"result": "FALLBACK"},
            speech=PARENT["speech"],
        )
    """
    if not fields:
        raise TypeError("fields is required for extract()")

    field_keys = [f.split(":")[0].strip().split(".")[-1] for f in fields]
    _default = default or {}

    # Initial loop state
    initial_state = {"error": "init"}
    for k in field_keys:
        initial_state[k] = _default.get(k)

    g = GraphOp.loop(
        until="error == None",
        max_iterations=retry + 1,
        **initial_state,
        **kwargs,
    )

    with g:
        _prompt = PromptOp(name="prompt", inputs={"*": PARENT})

        llm_inputs: Dict[str, Any] = {"messages": _prompt["messages"]}
        if response_format:
            llm_inputs["response_format"] = response_format

        _llm = LLMOp(
            name="llm",
            resource=resource,
            ratios=ratios,
            fallback=fallback,
            inputs=llm_inputs,
            delay=delay,
        )

        parser_inputs: Dict[str, Any] = {"text": _llm["content"]}

        # Dynamic validators: if validators is a Ref, pass via input for runtime resolution
        _is_ref = hasattr(validators, "is_ref") or (
            hasattr(validators, "__class__") and validators.__class__.__name__ == "Ref"
        )
        if _is_ref:
            parser_inputs["parser_validators"] = validators
            _static_validators = None
        else:
            _static_validators = validators

        _parser = ParserOp(
            name="parser",
            format=parser,
            extract=fields,
            validators=_static_validators,
            inputs=parser_inputs,
        )

        # Feed parser outputs back to loop state.
        # ParserOp returns error as output (not exception):
        # - success: {"field": value, "error": None}
        # - failure: {"field": None, "error": "message"}
        # When error, field values are None — loop keeps previous values
        # from initial state (defaults) because push ref only fires
        # when value is not None.
        _parser["error"] >> PARENT["error"]
        for key in field_keys:
            _parser[key] >> PARENT[key]

        START >> _prompt >> _llm >> _parser >> END

    return g
