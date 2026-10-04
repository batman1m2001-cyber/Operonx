"""Anthropic LLM provider — native async, no SDK dependency.

Uses httpx.AsyncClient to call Anthropic Messages API directly.
Converts between OpenAI message format (used by Operon internally)
and Anthropic's format (system separated, different SSE events).
"""

import json
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence, Union

from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_chunk import (
    ChatCompletionChunk,
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)
from openai.types.chat.chat_completion_chunk import (
    Choice as ChunkChoice,
)
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)
from openai.types.completion_usage import CompletionUsage

from operonx.core import LOGGER
from operonx.providers.llms.base import (
    BaseLLM,
    anthropic_cache_min_tokens,
    create_http_client,
    estimate_tokens,
    lift_cache_control,
    normalize_tool_call,
)
from operonx.providers.llms.config import LLMConfig


def _text_blocks(content: Any) -> list:
    """Content as a list of blocks, with no empty text block (Anthropic rejects one)."""
    if isinstance(content, list):
        return [dict(b) if isinstance(b, dict) else b for b in content]
    if content:
        return [{"type": "text", "text": str(content)}]
    return []


def _tool_use_block(call: Dict[str, Any]) -> Dict[str, Any]:
    """A tool call in any shape :func:`normalize_tool_call` reads → a ``tool_use`` block."""
    norm = normalize_tool_call(call)
    args = norm["args"]
    if not isinstance(args, dict):
        # Anthropic wants an object; keep what the model wrote rather
        # than lose it, so the history still says what was asked.
        args = {"_raw": args}
    return {"type": "tool_use", "id": norm["id"], "name": norm["name"], "input": args}


def _tool_result_block(message: Dict[str, Any]) -> Dict[str, Any]:
    """A ``role: "tool"`` message → a ``tool_result`` block."""
    content = message.get("content", "")
    if not isinstance(content, (str, list)):
        content = "" if content is None else str(content)
    block: Dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": message.get("tool_call_id", ""),
        "content": content,
    }
    if message.get("status") == "error" or message.get("is_error"):
        block["is_error"] = True
    return block


class _CitedSpans:
    """Where an answer's citations point, built as its text blocks arrive.

    Anthropic splits a cited answer into several text blocks; those resting
    on a source carry ``citations``. OpenAI's message has one ``content``
    string, so each cited block becomes a span of it::

        {"start": 25, "end": 53, "block_index": 1,
         "text": "refunds take 5 business days",
         "citations": [<Anthropic's citation dicts, unchanged>]}

    ``content[start:end] == text``. Only text blocks count toward offsets,
    as only they are joined into ``content``. Fed whole blocks
    (:meth:`add_block`) or stream events (:meth:`add_event`).
    """

    def __init__(self) -> None:
        self._length = 0
        self._spans: Dict[int, Dict[str, Any]] = {}
        self._text_blocks: set = set()

    def add_block(self, index: int, block: Dict[str, Any]) -> None:
        if block.get("type") != "text":
            return
        text = block.get("text", "")
        if block.get("citations"):
            self._spans[index] = {
                "start": self._length,
                "end": self._length + len(text),
                "block_index": index,
                "text": text,
                "citations": list(block["citations"]),
            }
        self._length += len(text)

    def add_event(self, event_type: str, event_data: Dict[str, Any]) -> None:
        index = event_data.get("index", 0)
        if event_type == "content_block_start":
            if (event_data.get("content_block") or {}).get("type") == "text":
                self._text_blocks.add(index)
            return
        if event_type != "content_block_delta" or index not in self._text_blocks:
            return
        delta = event_data.get("delta") or {}
        if delta.get("type") == "citations_delta" and delta.get("citation"):
            span = self._spans.setdefault(
                index,
                {
                    "start": self._length,
                    "end": self._length,
                    "block_index": index,
                    "text": "",
                    "citations": [],
                },
            )
            span["citations"].append(delta["citation"])
        elif delta.get("type") == "text_delta":
            text = delta.get("text", "")
            span = self._spans.get(index)
            if span is not None:
                span["text"] += text
                span["end"] += len(text)
            self._length += len(text)

    def result(self) -> Optional[List[Dict[str, Any]]]:
        """The spans in answer order, or None when nothing was cited."""
        return [self._spans[i] for i in sorted(self._spans)] or None


class AnthropicModel(BaseLLM):
    """Anthropic Claude provider using httpx.AsyncClient (no SDK)."""

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self.client = create_http_client(timeout=config.timeout)
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

        A message-level ``cache_control`` (how ``operonx.agents`` marks a
        breakpoint) becomes a content-block one — the only place Anthropic
        reads it. Rebuilding each message as ``{role, content}`` used to
        drop it, so the marker never reached the API.

        Several system messages are all kept, as one text block each: the
        last one used to overwrite the rest, which lost instructions and
        every breakpoint but the last.

        Tool calling is translated both ways of the conversation: an
        assistant's ``tool_calls`` become ``tool_use`` blocks after its
        text, and ``role: "tool"`` messages become ``tool_result`` blocks
        in a user message — consecutive ones in the *same* message, since
        Anthropic wants every result for a turn in the one that follows
        it. Keys Anthropic has no field for (``id``, ``name``, ``status``
        on an agent's messages) are dropped by the rebuild; a ``status``
        of ``"error"`` is kept as ``is_error``.

        Returns:
            (system or None, anthropic_messages list). ``system`` is the
            plain content for a single unmarked system message, else a
            list of text blocks.
        """
        systems: list = []
        messages: list = []
        for msg in openai_messages:
            msg = lift_cache_control(msg)
            if not isinstance(msg, dict):
                msg = {"role": getattr(msg, "role", "user"), "content": getattr(msg, "content", "")}
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                systems.append(content)
            elif role == "tool":
                block = _tool_result_block(msg)
                previous = messages[-1] if messages else None
                if previous and previous.get("_tool_results"):
                    previous["content"].append(block)
                else:
                    messages.append({"role": "user", "content": [block], "_tool_results": True})
            elif role == "assistant" and msg.get("tool_calls"):
                blocks = _text_blocks(content)
                blocks.extend(_tool_use_block(call) for call in msg["tool_calls"])
                messages.append({"role": "assistant", "content": blocks})
            else:
                messages.append({"role": role, "content": content})
        for message in messages:
            message.pop("_tool_results", None)

        if not systems:
            return None, messages
        if len(systems) == 1:
            return systems[0], messages
        blocks: list = []
        for content in systems:
            if isinstance(content, list):
                blocks.extend(content)
            elif content:
                blocks.append({"type": "text", "text": str(content)})
        return blocks, messages

    @staticmethod
    def _convert_tools(tools: Optional[List[dict]]) -> List[dict]:
        """OpenAI tool definitions → Anthropic's ``{name, description, input_schema}``.

        Both the nested ``{"type": "function", "function": {...}}`` form and
        a flat ``{name, description, parameters}`` one are read. A
        definition already in Anthropic's shape passes through.
        """
        converted = []
        for tool in tools or []:
            if not isinstance(tool, dict):
                continue
            if "input_schema" in tool:
                converted.append(tool)
                continue
            fn = tool.get("function") or tool
            entry: Dict[str, Any] = {
                "name": fn.get("name", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
            if fn.get("description"):
                entry["description"] = fn["description"]
            converted.append(entry)
        return converted

    @staticmethod
    def _convert_tool_choice(choice: Any) -> Optional[Dict[str, Any]]:
        """OpenAI ``tool_choice`` → Anthropic's.

        ``"required"`` is Anthropic's ``any``; a named function is its
        ``tool``. A dict already in Anthropic's shape passes through.
        """
        if choice is None:
            return None
        if isinstance(choice, str):
            return {"type": {"required": "any"}.get(choice, choice)}
        if isinstance(choice, dict):
            if choice.get("type") == "function":
                name = (choice.get("function") or {}).get("name") or choice.get("name")
                return {"type": "tool", "name": name}
            return choice
        return None

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
        """Anthropic response dict → OpenAI ChatCompletion.

        Native citations ride on the message as ``citations`` (see
        :class:`_CitedSpans`), set only when the answer cites something —
        OpenAI's message has no field for them, and joining the text
        blocks would otherwise lose which part rests on which source.
        """
        content_blocks = resp.get("content", [])
        text = "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )
        spans = _CitedSpans()
        for index, block in enumerate(content_blocks):
            spans.add_block(index, block)
        cited = spans.result()
        tool_calls = [
            ChatCompletionMessageToolCall(
                id=block.get("id", ""),
                type="function",
                function=Function(
                    name=block.get("name", ""),
                    arguments=json.dumps(block.get("input") or {}, ensure_ascii=False),
                ),
            )
            for block in content_blocks
            if block.get("type") == "tool_use"
        ]
        usage = resp.get("usage", {})

        # Anthropic reports input_tokens for NON-cached tokens only. The true
        # prompt size is input + cache_read + cache_creation. Cache hits are
        # surfaced in prompt_tokens_details.cached_tokens (OpenAI shape);
        # write counts are preserved in model_extra as cache_write_tokens.
        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_write = usage.get("cache_creation_input_tokens", 0) or 0
        prompt_tokens = input_tokens + cache_read + cache_write

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
                        tool_calls=tool_calls or None,
                        **({"citations": cited} if cited else {}),
                    ),
                    finish_reason=self._map_stop_reason(resp.get("stop_reason", "end_turn")),
                )
            ],
            usage=CompletionUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=output_tokens,
                total_tokens=prompt_tokens + output_tokens,
                prompt_tokens_details={"cached_tokens": cache_read},
                # Stash the Anthropic-only write count as a model_extra so
                # base.cache_metrics() can retrieve it without a new schema.
                cache_write_tokens=cache_write,
            ),
        )

    def _to_chunk(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        chunk_model: str,
        chunk_id: str,
        tool_index: Optional[Dict[int, int]] = None,
    ) -> Optional[ChatCompletionChunk]:
        """Anthropic SSE event → OpenAI ChatCompletionChunk.

        ``tool_index`` maps an Anthropic content-block index to the
        OpenAI tool-call index it streams as — the text block before a
        ``tool_use`` takes a block index, so the two do not line up. The
        caller keeps it across one stream; without it tool blocks are
        ignored.
        """
        if event_type == "content_block_start" and tool_index is not None:
            block = event_data.get("content_block") or {}
            if block.get("type") != "tool_use":
                return None
            index = tool_index.setdefault(event_data.get("index", 0), len(tool_index))
            return self._tool_call_chunk(
                chunk_id,
                chunk_model,
                ChoiceDeltaToolCall(
                    index=index,
                    id=block.get("id"),
                    type="function",
                    function=ChoiceDeltaToolCallFunction(name=block.get("name"), arguments=""),
                ),
            )
        if event_type == "content_block_delta":
            delta = event_data.get("delta", {})
            if delta.get("type") == "input_json_delta" and tool_index is not None:
                index = tool_index.get(event_data.get("index", 0))
                if index is None:
                    return None
                return self._tool_call_chunk(
                    chunk_id,
                    chunk_model,
                    ChoiceDeltaToolCall(
                        index=index,
                        function=ChoiceDeltaToolCallFunction(
                            arguments=delta.get("partial_json", "")
                        ),
                    ),
                )
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

    @staticmethod
    def _tool_call_chunk(
        chunk_id: str, chunk_model: str, call: ChoiceDeltaToolCall
    ) -> ChatCompletionChunk:
        return ChatCompletionChunk(
            id=chunk_id,
            created=int(time.time()),
            model=chunk_model,
            object="chat.completion.chunk",
            choices=[
                ChunkChoice(index=0, delta=ChoiceDelta(tool_calls=[call]), finish_reason=None)
            ],
        )

    # ── Build request body ──────────────────────────────────────────────

    def _maybe_cache_system(
        self,
        system: Any,
        messages: List[Dict[str, Any]],
        enabled: bool,
        cache_ttl: Optional[str] = None,
    ) -> Any:
        """Wrap the system block with cache_control if caching is worth it.

        Anthropic silently ignores cache_control when the prefix is below a
        per-model minimum, so we estimate the prefix size (system + messages)
        and only enable caching when it clears the safe threshold. Returns
        the system field shaped as a list-of-blocks when cached, or the
        original plain-string form otherwise.

        Args:
            cache_ttl: Optional TTL for the cache entry. Anthropic supports
                ``"5m"`` (default) and ``"1h"``. When set, the
                ``cache_control`` block includes a ``"ttl"`` field.
        """
        if not enabled or not system:
            return system
        prefix_tokens = estimate_tokens(system) + estimate_tokens(messages)
        if prefix_tokens < anthropic_cache_min_tokens(self.model):
            LOGGER.debug(
                "Anthropic cache skipped: prefix ~%d tokens < safe min %d for %s",
                prefix_tokens,
                anthropic_cache_min_tokens(self.model),
                self.model,
            )
            return system

        cc: Dict[str, str] = {"type": "ephemeral"}
        if cache_ttl:
            cc["ttl"] = cache_ttl

        if isinstance(system, str):
            return [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": cc,
                }
            ]
        if isinstance(system, list) and system:
            blocks = [dict(b) if isinstance(b, dict) else b for b in system]
            last = blocks[-1]
            if isinstance(last, dict):
                last.setdefault("cache_control", cc)
            return blocks
        return system

    def _build_request(
        self,
        messages: List[ChatCompletionMessageParam],
        stream: bool = False,
        cache: bool = True,
        cache_ttl: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        response_format = kwargs.get("response_format")
        if response_format and (response_format or {}).get("type", "text") != "text":
            # Dropping it would return an unconstrained answer that reads
            # as a constrained one — the caller would never know.
            raise ValueError(
                f"The anthropic backend cannot send response_format "
                f"{response_format.get('type')!r}: this request would come back unconstrained. "
                "For a schema-shaped answer force a tool call instead (declare "
                "structured_output: tool on the resource), or describe the shape in the prompt."
            )
        system, anthropic_messages = self._convert_messages(messages)
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens") or 4096,
        }
        if system:
            body["system"] = self._maybe_cache_system(
                system,
                anthropic_messages,
                cache,
                cache_ttl=cache_ttl,
            )
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
        tools = self._convert_tools(kwargs.get("tools"))
        if tools:
            body["tools"] = tools
        tool_choice = self._convert_tool_choice(kwargs.get("tool_choice"))
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        return body

    # ── Warmup (prompt caching) ───────────────────────────────────────

    async def warmup(
        self,
        system_prompt: str = "",
        cache_ttl: Optional[str] = None,
    ) -> None:
        """Pre-warm the Anthropic connection and seed the prompt cache.

        Sends a minimal 1-token request with ``cache_control`` on the
        system block so Anthropic caches it server-side. Subsequent calls
        with the same system prompt get a cache hit → faster inference.

        Args:
            cache_ttl: Optional TTL for the cache entry (``"5m"`` or
                ``"1h"``). Defaults to Anthropic's standard 5-minute TTL.
        """
        body: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "warmup"}],
        }
        if system_prompt:
            cc: Dict[str, str] = {"type": "ephemeral"}
            if cache_ttl:
                cc["ttl"] = cache_ttl
            body["system"] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": cc,
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
            cache=kwargs.get("cache", True),
            cache_ttl=kwargs.get("cache_ttl"),
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stop=stop,
            tools=tools,
            tool_choice=kwargs.get("tool_choice"),
            response_format=response_format,
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
        Citations (``citations_delta`` events) are collected as the text
        streams and ride on the final chunk's delta as ``citations``, in
        the shape :meth:`generate` gives them.
        """
        body = self._build_request(
            messages,
            stream=True,
            cache=kwargs.get("cache", True),
            cache_ttl=kwargs.get("cache_ttl"),
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stop=stop,
            tools=tools,
            tool_choice=kwargs.get("tool_choice"),
            response_format=response_format,
        )
        url = f"{self.base_url}/v1/messages"
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        chunk_model = self.model
        tool_index: Dict[int, int] = {}
        spans = _CitedSpans()

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

                    spans.add_event(event_type, event_data)
                    chunk = self._to_chunk(
                        event_type, event_data, chunk_model, chunk_id, tool_index
                    )
                    if chunk and event_type == "message_delta":
                        cited = spans.result()
                        if cited:
                            chunk.choices[0].delta.citations = cited
                    if chunk:
                        yield chunk
