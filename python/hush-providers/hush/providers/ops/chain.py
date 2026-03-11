"""chain() — @graph factory that builds PromptOp + LLMOp + optional ParserOp."""

from typing import Any, Dict, List, Optional, Union

from hush.core.ops import END, PARENT, START, ParserOp, graph
from hush.providers.ops.llm import LLMOp
from hush.providers.ops.prompt import PromptOp


@graph
def chain(
    template=None,
    resource: Optional[Union[str, List[str]]] = None,
    ratios: Optional[List[float]] = None,
    fallback: Optional[List[str]] = None,
    extract: Optional[List[str]] = None,
    parser: str = "xml",
    response_format: Optional[Dict[str, Any]] = None,
):
    """Build a PromptOp -> LLMOp -> (optional ParserOp) graph.

    The most common building block for LLM workflows. Operates in two modes:

    * **Text mode** (no ``extract``): returns raw LLM output (content, role, ...).
    * **Structured mode** (``extract`` provided): parses the LLM output into
      typed fields via ParserOp.

    Config params (resource, extract, etc.) are static -- passed through by @graph.
    Template variables (query=PARENT["q"]) arrive as graph inputs via PARENT
    wildcard on PromptOp.

    Args:
        template: Prompt template (str, dict, or list). See ``PromptOp``.
        resource: Resource key(s) for LLM in ResourceHub.
            - Single string: ``"gpt-4"``
            - List for load balancing: ``["gpt-4", "claude-3"]``
        ratios: Weight ratios for load balancing (must sum to 1.0).
        fallback: Fallback resource keys, tried in order on failure.
        extract: Output fields for structured parsing
            (e.g., ``["category: str", "confidence: float"]``).
        parser: Parser format (``"xml"``, ``"json"``, ``"yaml"``).
        response_format: OpenAI response format for JSON mode.

    Keyword Args:
        **kwargs: Template variables (Ref or static) and init kwargs
            (``name=``, ``outputs=``, ``contain_generation=``, etc.).

    Returns:
        A GraphOp containing the prompt -> LLM -> (parser) pipeline.

    Example::

        chat = chain(
            resource="gpt-4o",
            template={"system": "You are helpful.", "user": "{query}"},
            query=PARENT["query"],
        )

        # Structured output
        parsed = chain(
            resource="gpt-4o",
            template="Classify: {text}",
            extract=["category: str", "confidence: float"],
            text=PARENT["text"],
        )
    """
    _prompt = PromptOp(name="prompt", inputs={"*": PARENT})

    llm_inputs: Dict[str, Any] = {"messages": _prompt["messages"]}
    if response_format:
        llm_inputs["response_format"] = response_format

    if extract:
        # Structured output: Prompt -> LLM -> Parser
        _llm = LLMOp(
            name="llm",
            resource=resource,
            ratios=ratios,
            fallback=fallback,
            inputs=llm_inputs,
        )

        _parser = ParserOp(
            name="parser",
            format=parser,
            extract=extract,
            inputs={"text": _llm["content"]},
            outputs={"*": PARENT},
        )

        START >> _prompt >> _llm >> _parser >> END
    else:
        # Text generation: Prompt -> LLM
        _llm = LLMOp(
            name="llm",
            resource=resource,
            ratios=ratios,
            fallback=fallback,
            inputs=llm_inputs,
            outputs={"*": PARENT},
        )

        START >> _prompt >> _llm >> END
