"""Provider-neutral chat message type.

`ChatMessage` is a `TypedDict` chosen over a Pydantic model so it stays
zero-cost at runtime — providers that need validation do their own at
the boundary; pure-compute graphs that pass messages through never pay
a model-construction cost.
"""

from typing import Literal, TypedDict

ChatRole = Literal["system", "user", "assistant", "tool"]


class ChatMessage(TypedDict, total=False):
    """A single message in a chat conversation.

    Provider-neutral shape; backends translate to / from this at the
    LLMOp boundary.

    Required fields:
        role: One of ``"system"``, ``"user"``, ``"assistant"``,
            ``"tool"``.
        content: The message body. ``str`` for plain text;
            providers may accept richer structured shapes (tool calls,
            multi-modal parts) via opt-in fields below.

    Optional fields:
        name: Speaker identifier (for tool replies and named system
            prompts).
        tool_call_id: When ``role == "tool"``, the id of the tool call
            this message responds to.
        tool_calls: When ``role == "assistant"``, the list of tool
            calls the model is requesting. Shape is
            provider-specific; converter layers normalise this in v0.7.
    """

    role: ChatRole
    content: str
    name: str
    tool_call_id: str
    tool_calls: list
