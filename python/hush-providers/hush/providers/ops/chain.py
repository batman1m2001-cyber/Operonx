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
# extract() — structured output with retry + validation
# =============================================================================


def extract(
    # -- Op constructor config (fixed per op instance) --
    resource: Optional[Union[str, List[str]]] = None,
    ratios: Optional[List[float]] = None,
    fallback: Optional[List[str]] = None,
    fields: Optional[List[str]] = None,
    parser: str = "xml",
    delay: float = 0,
    # -- Op runtime inputs (can be static or Ref) --
    template: Any = None,
    response_format: Optional[Dict[str, Any]] = None,
    validators: Optional[Dict[str, list]] = None,
    # -- Loop control --
    retry: int = 0,
    # -- Extra kwargs: template vars (speech=PARENT["speech"]), etc. --
    **kwargs,
) -> GraphOp:
    """Prompt → LLM → Parser with retry via GraphOp.loop.

    Four kinds of params flow through this function:

    1. **Op constructor config** — resource, fields, parser, delay, ratios, fallback.
       Passed to op constructors (LLMOp, ParserOp). Fixed across retries.

    2. **Op runtime inputs** — template, validators, response_format.
       Passed via op ``inputs={...}``. Can be static values or Refs (dynamic).

    3. **Loop control** — retry.
       retry → max_iterations. Defaults extracted from validators (@-prefixed values).

    4. **Extra kwargs** — template vars (e.g. speech=PARENT["speech"]).
       Forwarded into GraphOp.loop → available as PARENT vars for PromptOp.

    GraphOp.loop receives: error="init" + **default + **kwargs as initial state.
    On each iteration: PromptOp → LLMOp → ParserOp. Parser feeds error + fields
    back to PARENT. Loop continues until error == None or max retries exhausted.

    Example::

        e = extract(
            resource="claude-haiku",
            template="Classify: {speech}",
            fields=["result: str"],
            parser="xml",
            retry=2,
            validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
            speech=PARENT["speech"],
        )
    """
    if not fields:
        raise TypeError("fields is required for extract()")

    # Loop state: error="init" triggers first iteration (until="error == None"),
    # **kwargs carries template vars and other graph inputs.
    # Defaults handled by ParserOp via @-prefixed validators (e.g. "@FALLBACK").
    with GraphOp.loop(
        until="error == None",
        max_iterations=retry + 1,
        error="init",
        **kwargs,
    ) as g:
        # 1. Build prompt from template + PARENT vars (template vars from **kwargs)
        _prompt = PromptOp(name="prompt", inputs={"template": template, "*": PARENT})

        # 2. Call LLM
        _llm = LLMOp(
            name="llm",
            resource=resource,
            ratios=ratios,
            fallback=fallback,
            inputs={"messages": _prompt["messages"], "response_format": response_format},
            delay=delay,
        )

        # 3. Parse + validate LLM output
        _parser = ParserOp(
            name="parser",
            format=parser,
            extract=fields,
            inputs={"text": _llm["content"], "validators": validators},
            outputs={"*": PARENT},  # forward parsed fields to loop state
        )

        # Feed error back to loop state for retry decision
        _parser["error"] >> PARENT["error"]

        START >> _prompt >> _llm >> _parser >> END

    return g
