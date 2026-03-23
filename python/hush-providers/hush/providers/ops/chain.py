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

    _llm = LLMOp(
        name="llm",
        resource=resource,
        ratios=ratios,
        fallback=fallback,
        inputs={"messages": _prompt["messages"], "response_format": response_format},
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
    default: Dict[str, Any] = {},
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

    with GraphOp.loop(
        until="error == None",
        max_iterations=retry + 1,
        error="init",
        **default,
        **kwargs,
    ) as g:
        _prompt = PromptOp(name="prompt", inputs={"*": PARENT})

        _llm = LLMOp(
            name="llm",
            resource=resource,
            ratios=ratios,
            fallback=fallback,
            inputs={"messages": _prompt["messages"], "response_format": response_format},
            delay=delay,
        )

        _parser = ParserOp(
            name="parser",
            format=parser,
            extract=fields,
            inputs={"text": _llm["content"], "validators": validators},
            outputs={"*": PARENT},
        )

        _parser["error"] >> PARENT["error"]

        START >> _prompt >> _llm >> _parser >> END

    return g
