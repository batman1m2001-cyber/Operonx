"""ask() — structured-output workflow (LLM → Parser), with optional retry."""

from typing import Any, Dict, List, Optional, Union

from operonx.core.ops import END, PARENT, START, ParserOp, graph
from operonx.providers.ops.llm import LLMOp


@graph
def ask(
    # Loop state (before *) — only these become loop state variables
    error: str = None,
    *,
    # Config (keyword-only) — passed directly, NOT loop state
    prompt: Any = None,
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
    """LLM → Parser graph for structured extraction.

    Simple mode (no retry)::

        a = ask(
            resource="claude-haiku",
            prompt="Classify: {speech}",
            fields=["result: str"],
            parser="xml",
            validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
            speech=PARENT["speech"],
        )

    Retry mode (pass ``until=``)::

        a = ask(
            resource="claude-haiku",
            prompt="Classify: {speech}",
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

    _llm = LLMOp(
        name="llm",
        resource=resource,
        ratios=ratios,
        fallback=fallback,
        inputs={"prompt": prompt, "response_format": response_format, "*": PARENT},
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

    START >> _llm >> _parser >> END
