"""Anthropic LLM provider — native async, no SDK dependency.

Uses httpx.AsyncClient to call Anthropic Messages API directly.
Converts between OpenAI message format (used by Hush internally)
and Anthropic's format (system separated, different SSE events).
"""

import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence, Union

from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import (
    ChatCompletionChunk,
    ChoiceDelta,
)
from openai.types.chat.chat_completion_chunk import (
    Choice as ChunkChoice,
)
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.completion_usage import CompletionUsage

from hush.core import LOGGER
from hush.providers.llms.base import BaseLLM, create_http_client
from hush.providers.llms.config import LLMConfig


class AnthropicModel(BaseLLM):
    """Anthropic Claude provider using httpx.AsyncClient (no SDK)."""

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self.client = create_http_client()
        self.base_url = getattr(config, "base_url", "https://api.anthropic.com").rstrip("/")
        self.api_key = config.api_key
        self.model = config.model
        self.anthropic_version = getattr(config, "anthropic_version", "2023-06-01")

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
        }

    # ── Message conversion ──────────────────────────────────────────────

    @staticmethod
    def _convert_messages(
        openai_messages: List[ChatCompletionMessageParam],
    ) -> tuple:
        """Convert OpenAI messages → Anthropic format.

        Anthropic requires system as a separate top-level field,
        not inside the messages array.

        Returns:
            (system_text or None, anthropic_messages list)
        """
        system = None
        messages = []
        for msg in openai_messages:
            role = (
                msg.get("role", "user") if isinstance(msg, dict) else getattr(msg, "role", "user")
            )
            content = (
                msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            )
            if role == "system":
                system = content
            else:
                messages.append({"role": role, "content": content})
        return system, messages

    @staticmethod
    def _map_stop_reason(reason: str) -> str:
        """Anthropic stop_reason → OpenAI finish_reason."""
        return {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
        }.get(reason, reason or "stop")

    # ── Response conversion ─────────────────────────────────────────────

    def _to_chat_completion(self, resp: Dict[str, Any]) -> ChatCompletion:
        """Anthropic response dict → OpenAI ChatCompletion."""
        content_blocks = resp.get("content", [])
        text = "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )
        usage = resp.get("usage", {})

        return ChatCompletion(
            id=resp.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
            created=int(time.time()),
            model=resp.get("model", self.model),
            object="chat.completion",
            choices=[
                Choice(
                    index=0,
                    message=ChatCompletionMessage(
                        role="assistant",
                        content=text,
                    ),
                    finish_reason=self._map_stop_reason(resp.get("stop_reason", "end_turn")),
                )
            ],
            usage=CompletionUsage(
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
                total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            ),
        )

    def _to_chunk(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        chunk_model: str,
        chunk_id: str,
    ) -> Optional[ChatCompletionChunk]:
        """Anthropic SSE event → OpenAI ChatCompletionChunk."""
        if event_type == "content_block_delta":
            delta = event_data.get("delta", {})
            if delta.get("type") == "text_delta":
                return ChatCompletionChunk(
                    id=chunk_id,
                    created=int(time.time()),
                    model=chunk_model,
                    object="chat.completion.chunk",
                    choices=[
                        ChunkChoice(
                            index=0,
                            delta=ChoiceDelta(content=delta.get("text", "")),
                            finish_reason=None,
                        )
                    ],
                )
        elif event_type == "message_delta":
            delta = event_data.get("delta", {})
            usage = event_data.get("usage", {})
            return ChatCompletionChunk(
                id=chunk_id,
                created=int(time.time()),
                model=chunk_model,
                object="chat.completion.chunk",
                choices=[
                    ChunkChoice(
                        index=0,
                        delta=ChoiceDelta(),
                        finish_reason=self._map_stop_reason(delta.get("stop_reason", "")),
                    )
                ],
                usage=CompletionUsage(
                    prompt_tokens=usage.get("input_tokens", 0),
                    completion_tokens=usage.get("output_tokens", 0),
                    total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                )
                if usage
                else None,
            )
        return None

    # ── Build request body ──────────────────────────────────────────────

    def _build_request(
        self,
        messages: List[ChatCompletionMessageParam],
        stream: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        system, anthropic_messages = self._convert_messages(messages)
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens") or 4096,
        }
        if system:
            body["system"] = system
        if stream:
            body["stream"] = True
        # Anthropic: temperature and top_p are mutually exclusive
        if kwargs.get("temperature") is not None:
            body["temperature"] = kwargs["temperature"]
        elif kwargs.get("top_p") is not None:
            body["top_p"] = kwargs["top_p"]
        if kwargs.get("stop") is not None:
            stop = kwargs["stop"]
            body["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
        return body

    # ── Warmup (prompt caching) ───────────────────────────────────────

    async def warmup(self, system_prompt: str = "") -> None:
        """Pre-warm the Anthropic connection and seed the prompt cache.

        Sends a minimal 1-token request with ``cache_control`` on the
        system block so Anthropic caches it server-side. Subsequent calls
        with the same system prompt get a cache hit → faster inference.
        """
        body: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "warmup"}],
        }
        if system_prompt:
            body["system"] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        url = f"{self.base_url}/v1/messages"
        resp = await self.client.post(url, headers=self._headers(), json=body)
        if resp.status_code != 200:
            LOGGER.warning("Anthropic warmup failed (%d): %s", resp.status_code, resp.text[:200])

    # ── Core methods ────────────────────────────────────────────────────

    async def generate(
        self,
        messages: List[ChatCompletionMessageParam],
        temperature: float = 0.0,
        top_p: float = 0.1,
        n: Optional[int] = None,
        stop: Optional[Union[str, Sequence[str]]] = None,
        max_tokens: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        response_format: Optional[dict] = None,
        tools: Optional[dict] = None,
        **kwargs,
    ) -> ChatCompletion:
        """Non-streaming Anthropic Messages API call."""
        body = self._build_request(
            messages,
            stream=False,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stop=stop,
        )
        url = f"{self.base_url}/v1/messages"
        resp = await self.client.post(url, headers=self._headers(), json=body)

        if resp.status_code != 200:
            LOGGER.error(f"Anthropic API error {resp.status_code}: {resp.text}")
            resp.raise_for_status()

        return self._to_chat_completion(resp.json())

    async def stream(
        self,
        messages: List[ChatCompletionMessageParam],
        temperature: float = 0.0,
        top_p: float = 0.1,
        n: Optional[int] = None,
        stop: Optional[Union[str, Sequence[str]]] = None,
        max_tokens: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        response_format: Optional[dict] = None,
        tools: Optional[dict] = None,
        **kwargs,
    ) -> AsyncGenerator[ChatCompletionChunk, None]:
        """Streaming Anthropic Messages API call.

        Parses Anthropic SSE events and yields OpenAI-compatible chunks.
        """
        body = self._build_request(
            messages,
            stream=True,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stop=stop,
        )
        url = f"{self.base_url}/v1/messages"
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        chunk_model = self.model

        async with self.client.stream("POST", url, headers=self._headers(), json=body) as resp:
            if resp.status_code != 200:
                body_text = await resp.aread()
                LOGGER.error(f"Anthropic streaming error {resp.status_code}: {body_text.decode()}")
                resp.raise_for_status()

            event_type = None
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue

                if line.startswith("event: "):
                    event_type = line[7:]
                    continue

                if line.startswith("data: ") and event_type:
                    import json

                    try:
                        event_data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    # Extract model from message_start
                    if event_type == "message_start":
                        msg = event_data.get("message", {})
                        chunk_model = msg.get("model", self.model)
                        chunk_id = msg.get("id", chunk_id)
                        continue

                    chunk = self._to_chunk(event_type, event_data, chunk_model, chunk_id)
                    if chunk:
                        yield chunk
