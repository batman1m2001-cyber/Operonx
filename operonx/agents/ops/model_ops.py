"""Adapting ``LLMOp`` to the shape the ReAct loop expects.

``build_react_agent`` asks its ``call_model`` for three things:
``assistant_message``, ``tool_calls`` and ``done``. ``LLMOp`` produces
``content``, ``tool_calls`` and ``finish_reason``. This is the seam
between them, and it is worth a named module rather than a lambda in a
docstring because two of the three conversions carry a decision:

**``done`` is derived from tool calls, not from ``finish_reason``.**
Providers disagree about the stop reason on a tool-calling turn — some
say ``tool_calls``, some ``stop``, some ``length`` when the call was
truncated. What is unambiguous is whether the model asked for a tool, so
that is what ends the loop.

**The assistant message keeps its ``tool_calls``.** Sending back a turn
with the text but not the calls it made leaves the following tool
messages answering nothing, and the provider rejects the conversation.

``finish_reason`` is still reported so a caller can tell a clean stop
from a truncated one — a response cut at ``length`` is not a finished
answer, even though the loop treats both as done.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional
from uuid import uuid4

from operonx.core.ops.transform.func_op import op

__all__ = ["adapt_llm_output", "make_llm_caller"]

#: Stop reasons that mean the answer is incomplete rather than finished.
TRUNCATED_REASONS = frozenset({"length", "max_tokens", "content_filter"})


@op
def adapt_llm_output(
    content: str = "",
    tool_calls: Optional[list] = None,
    finish_reason: str = "",
) -> dict:
    """Convert one ``LLMOp`` result into the ReAct loop's contract."""
    calls = [c for c in (tool_calls or []) if isinstance(c, dict)]

    # A fresh id per turn, because `add_messages` upserts on it. A stable
    # id like f"assistant-{turn}" made every turn overwrite the last: the
    # tool-calling turn vanished from the history, leaving its tool
    # messages answering nothing, and the conversation came back as
    # user → tool → assistant. Found only against a live model — the
    # scripted ones all happened to use distinct ids.
    message: dict = {
        "id": f"assistant-{uuid4().hex[:12]}",
        "role": "assistant",
        "content": content or "",
    }
    if calls:
        # Without these the tool messages that follow answer nothing and
        # the provider rejects the whole conversation.
        message["tool_calls"] = calls

    return {
        "assistant_message": [message],
        "tool_calls": calls,
        # Asked for a tool → keep going. Deriving this from finish_reason
        # would make the loop provider-specific for no benefit.
        "done": not calls,
        "finish_reason": finish_reason or "",
        "truncated": finish_reason in TRUNCATED_REASONS,
    }


def make_llm_caller(
    resource: str,
    *,
    tools: Optional[List[dict]] = None,
    **llm_kwargs: Any,
) -> Callable:
    """Build a ``call_model`` for :func:`~operonx.agents.graphs.build_react_agent`.

    Args:
        resource: ResourceHub key **without** the ``llm:`` prefix —
            ``"qwen"`` for a ``llm: qwen:`` block. ``LLMOp`` prepends it,
            so passing ``"llm:qwen"`` looks for ``llm:llm:qwen``.
        tools: Tool definitions from
            :func:`~operonx.agents.tool.get_tool_definitions`. Omit for an
            agent with no tools — the loop then ends after one turn,
            which is a valid degenerate case rather than an error.
        **llm_kwargs: Passed to ``LLMOp.of`` — ``temperature``,
            ``max_tokens``, ``fallback``, and so on.

    Returns:
        A callable taking ``messages=`` and returning the adapter node.

    Note:
        The provider must actually support tool calling. Several
        OpenAI-compatible gateways accept ``tools`` and silently answer in
        prose instead — vLLM, for instance, needs
        ``--enable-auto-tool-choice`` and rejects ``tool_choice="auto"``
        without it. An agent pointed at such a server never calls a tool
        and looks merely unhelpful, so verify against the real endpoint
        before trusting it.
    """

    def call_model(messages: Any = None):
        from operonx.core.ops.base import END, PARENT, START
        from operonx.core.ops.graph._decorators import graph
        from operonx.providers.ops import LLMOp

        # A **subgraph**, not two loose ops. The ReAct loop's back-edge
        # makes the model node part of a cycle, and the cycle rewrite
        # extracts that cycle into a hidden loop graph. Returning the
        # adapter alone left `LLMOp` outside the extracted body while the
        # adapter went inside, so the adapter's refs pointed out of scope
        # and the build failed with "references 'llm' which is outside
        # this graph's scope". Nesting them makes the pair a single node
        # that moves together.
        @graph
        def model_call(messages=None):
            # `messages=`, never `prompt=`. A conversation is data, and
            # `prompt=` formats what it is given — which used to mean every
            # brace in the history (a JSON tool result, pasted code, the
            # model's own tool-call arguments) became a template variable
            # that did not exist, killing the *next* model call. This layer
            # carried an `_escape_braces` walk to survive that; `messages=`
            # made it unnecessary.
            llm = LLMOp.of(
                resource=resource,
                messages=messages,
                tools=tools,
                **llm_kwargs,
            )
            adapted = adapt_llm_output(
                content=llm["content"],
                tool_calls=llm["tool_calls"],
                finish_reason=llm["finish_reason"],
            )
            adapted["assistant_message"] >> PARENT["assistant_message"]
            adapted["tool_calls"] >> PARENT["tool_calls"]
            adapted["done"] >> PARENT["done"]
            adapted["finish_reason"] >> PARENT["finish_reason"]
            adapted["truncated"] >> PARENT["truncated"]
            START >> llm >> adapted >> END

        return model_call(messages=messages)

    return call_model
