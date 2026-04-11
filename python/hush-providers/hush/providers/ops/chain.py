"""chat() and ask() — LLM workflow building blocks.

- chat(): text generation (Prompt → LLM)
- ask(): structured output (Prompt → LLM → Parser), with optional retry via until=
"""

from typing import Any, Dict, List, Optional, Union

from hush.core.ops import END, PARENT, START, ParserOp, graph
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

    Returns raw LLM output: content, role, model_used, usage, extras, etc.

    Example::

        c = chat(
            resource="claude-haiku",
            template={"system": "You are helpful.", "user": "{query}"},
            query=PARENT["query"],
        )
    """
    _prompt = PromptOp(name="prompt", inputs={"template": template, "*": PARENT})

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
# ask() — structured output with optional retry
# =============================================================================


@graph
def ask(
    # Loop state (before *) — only these become loop state variables
    error: str = None,
    *,
    # Config (keyword-only) — passed directly, NOT loop state
    template: Any = None,
    resource: Optional[Union[str, List[str]]] = None,
    ratios: Optional[List[float]] = None,
    fallback: Optional[List[str]] = None,
    fields: Optional[List[str]] = None,
    parser: str = "xml",
    delay: float = 0,
    response_format: Optional[Dict[str, Any]] = None,
    validators: Optional[Dict[str, list]] = None,
    until: str = None,
    max_iterations: int = 2,
) -> Any:
    """Prompt → LLM → Parser graph for structured extraction.

    Simple mode (no retry)::

        a = ask(
            resource="claude-haiku",
            template="Classify: {speech}",
            fields=["result: str"],
            parser="xml",
            validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
            speech=PARENT["speech"],
        )

    Retry mode (pass ``until=``)::

        a = ask(
            resource="claude-haiku",
            template="Classify: {speech}",
            fields=["result: str"],
            parser="xml",
            validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
            until="error == None",
            max_iterations=3,
            error="init",
            speech=PARENT["speech"],
        )
    """
    if not fields:
        raise TypeError("fields is required for ask()")

    _prompt = PromptOp(name="prompt", inputs={"template": template, "*": PARENT})

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

    # In loop mode, feed error back to PARENT for until check
    if error is not None:
        _parser["error"] >> PARENT["error"]

    START >> _prompt >> _llm >> _parser >> END
