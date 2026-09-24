"""Databricks-proxied LLM subclasses.

Databricks exposes hosted models through two different OpenAI-compatible
endpoints on the same workspace host, and they do **not** accept the same
request body:

``/serving-endpoints``
    Anthropic Claude. The proxy translates Anthropic content-parts and
    ``cache_control`` blocks through to the native Anthropic API, so
    prompt caching works if you leave those fields intact.

``/ai-gateway/mlflow/v1``
    Google Gemini via the system model catalog (``system.ai.*``). A
    strict OpenAI-compat surface: it rejects Anthropic-only fields with
    ``401 - Credential was not sent or was of an unsupported type``,
    which sends you looking at your token for a message-shape problem.

The suffix belongs in ``resources.yaml`` under ``base_url`` — these
classes never append or rewrite it. An explicit URL keeps the config
readable and lets an operator point at another tenant or endpoint
version without editing this file.

Two classes rather than one with a mode flag: the Anthropic and Gemini
contracts on Databricks differ in message format *and* cache semantics,
and show no sign of converging.
"""

from typing import Any, AsyncGenerator, List, Optional, Sequence, Union

from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk

from operonx.providers.llms.openai import OpenAISDKModel

#: Anthropic's hard cap on cache breakpoints per request.
ANTHROPIC_CACHE_CONTROL_LIMIT = 4


class DatabricksAnthropic(OpenAISDKModel):
    """Claude via Databricks ``/serving-endpoints``.

    Keeps Anthropic-style ``cache_control`` blocks in content parts — the
    proxy forwards them to native prompt caching — and enforces the
    4-breakpoint limit locally, where the error can say what went wrong
    rather than arriving as an API 400.

    ``config.base_url`` must already carry the ``/serving-endpoints``
    suffix.
    """

    @staticmethod
    def _count_cache_breakpoints(messages: List[ChatCompletionMessageParam]) -> int:
        n = 0
        for m in messages:
            content = m.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("cache_control"):
                    n += 1
        return n

    def _validate_cache_control(self, messages: List[ChatCompletionMessageParam]) -> None:
        n = self._count_cache_breakpoints(messages)
        if n > ANTHROPIC_CACHE_CONTROL_LIMIT:
            raise ValueError(
                f"Anthropic cache_control limit exceeded: {n} breakpoints "
                f"(max {ANTHROPIC_CACHE_CONTROL_LIMIT})"
            )

    async def generate(
        self,
        messages: List[ChatCompletionMessageParam],
        temperature: Optional[float] = 0.0,
        top_p: Optional[float] = 0.1,
        n: Optional[int] = None,
        stop: Optional[Union[str, Sequence[str]]] = None,
        max_tokens: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        response_format: Optional[dict] = None,
        tools: Optional[dict] = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        self._validate_cache_control(messages)
        return await super().generate(
            messages,
            temperature=temperature,
            top_p=top_p,
            n=n,
            stop=stop,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            response_format=response_format,
            tools=tools,
            **kwargs,
        )

    async def stream(
        self,
        messages: List[ChatCompletionMessageParam],
        temperature: Optional[float] = 0.0,
        top_p: Optional[float] = 0.1,
        n: Optional[int] = None,
        stop: Optional[Union[str, Sequence[str]]] = None,
        max_tokens: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        response_format: Optional[dict] = None,
        tools: Optional[dict] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatCompletionChunk, None]:
        self._validate_cache_control(messages)
        async for chunk in super().stream(
            messages,
            temperature=temperature,
            top_p=top_p,
            n=n,
            stop=stop,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            response_format=response_format,
            tools=tools,
            **kwargs,
        ):
            yield chunk


class DatabricksGemini(OpenAISDKModel):
    """Gemini via Databricks ``/ai-gateway/mlflow/v1``.

    Strips Anthropic-only request fields (``cache_control``) and flattens
    multi-part text content to a plain string before the request leaves —
    the AI Gateway rejects both, and the error it returns names
    credentials rather than the message shape.

    ``config.base_url`` must already carry the ``/ai-gateway/mlflow/v1``
    suffix.
    """

    @staticmethod
    def _flatten_message(m: dict) -> dict:
        """Collapse an Anthropic-style content list into a plain string.

        Non-list content passes through untouched. ``cache_control`` and
        any non-text part (image_url, …) is dropped — this endpoint has
        nowhere to put them.
        """
        content = m.get("content")
        if not isinstance(content, list):
            return m
        text_parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return {**m, "content": "\n".join(text_parts)}

    def _normalize_messages(
        self, messages: List[ChatCompletionMessageParam]
    ) -> List[ChatCompletionMessageParam]:
        return [self._flatten_message(m) for m in messages]

    async def generate(
        self,
        messages: List[ChatCompletionMessageParam],
        temperature: Optional[float] = 0.0,
        top_p: Optional[float] = 0.1,
        n: Optional[int] = None,
        stop: Optional[Union[str, Sequence[str]]] = None,
        max_tokens: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        response_format: Optional[dict] = None,
        tools: Optional[dict] = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        return await super().generate(
            self._normalize_messages(messages),
            temperature=temperature,
            top_p=top_p,
            n=n,
            stop=stop,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            response_format=response_format,
            tools=tools,
            **kwargs,
        )

    async def stream(
        self,
        messages: List[ChatCompletionMessageParam],
        temperature: Optional[float] = 0.0,
        top_p: Optional[float] = 0.1,
        n: Optional[int] = None,
        stop: Optional[Union[str, Sequence[str]]] = None,
        max_tokens: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        response_format: Optional[dict] = None,
        tools: Optional[dict] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatCompletionChunk, None]:
        async for chunk in super().stream(
            self._normalize_messages(messages),
            temperature=temperature,
            top_p=top_p,
            n=n,
            stop=stop,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            response_format=response_format,
            tools=tools,
            **kwargs,
        ):
            yield chunk
