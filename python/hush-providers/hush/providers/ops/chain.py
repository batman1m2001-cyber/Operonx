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


@graph
def extract(
    resource: Optional[Union[str, List[str]]] = None,
    ratios: Optional[List[float]] = None,
    fallback: Optional[List[str]] = None,
    fields: Optional[List[str]] = None,
    parser: str = "xml",
    delay: float = 0,
    template: Any = None,
    response_format: Optional[Dict[str, Any]] = None,
    validators: Optional[Dict[str, list]] = None,
) -> Any:
    """Prompt → LLM → Parser graph for structured extraction.

    Simple @graph: no retry, no loop. Just Prompt → LLM → Parser → END.

    Example::

        e = extract(
            resource="claude-haiku",
            template="Classify: {speech}",
            fields=["result: str"],
            parser="xml",
            validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
            speech=PARENT["speech"],
        )
    """
    if not fields:
        raise TypeError("fields is required for extract()")

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

    START >> _prompt >> _llm >> _parser >> END


def extract_with_retry(
    resource: Optional[Union[str, List[str]]] = None,
    ratios: Optional[List[float]] = None,
    fallback: Optional[List[str]] = None,
    fields: Optional[List[str]] = None,
    parser: str = "xml",
    delay: float = 0,
    template: Any = None,
    response_format: Optional[Dict[str, Any]] = None,
    validators: Optional[Dict[str, list]] = None,
    retry: int = 0,
    **kwargs,
) -> GraphOp:
    """Prompt → LLM → Parser with retry via GraphOp.loop.

    Use extract() for simple cases. Use _extract() when retry is needed.
    """
    if not fields:
        raise TypeError("fields is required for extract_with_retry()")

    with GraphOp.loop(
        until="error == None",
        max_iterations=retry + 1,
        error="init",
        template=template,
        **kwargs,
    ) as g:
        _prompt = PromptOp(name="prompt", inputs={"template": PARENT["template"], "*": PARENT})

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
