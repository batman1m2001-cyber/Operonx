"""Provider-neutral type definitions for Operonx.

These types are the canonical message / response shapes the engine
exchanges with caller code. Provider backends (OpenAI, Anthropic,
Gemini, …) translate to / from these at the LLMOp boundary so callers
do not depend on a specific SDK's types.

Today the LLMOp boundary still emits `openai.types.chat.ChatCompletion`
for backwards compatibility — this module exists so converter layers
have a place to land in v0.7. See `REFACTOR_post_v0.6.2.md`.
"""

from operonx.core.types.chat import ChatMessage, ChatRole

__all__ = [
    "ChatMessage",
    "ChatRole",
]
