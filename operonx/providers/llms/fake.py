"""``api_type: fake`` — a scripted LLM for tests, templates and demos.

    llm:assistant:
      api_type: fake
      script:
        - "Hello! How can I help?"                    # a text answer
        - tool_calls: [{name: lookup, args: {id: 7}}] # a tool call
        - {status: 429}                               # the provider says no
        - {delay: 0.5, text: "slow, but here"}        # an answer that takes time
        - {echo: "Echo: "}                            # the last user message back

Each call takes the next turn; the last one repeats. ``stream`` sends a
turn's text in chunks of ``chunk_size`` characters, and its tool calls in
one chunk, as a real provider's stream would. A ``status`` turn raises the
error the OpenAI SDK raises for that status — what retries, fallbacks and
error edges see from a real one. Nothing goes over the network.

``calls`` keeps the messages of every call, for a test to look at.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List

from operonx.providers.llms.base import BaseLLM

__all__ = ["FakeLLM"]


def _turn(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        return {"text": raw}
    if isinstance(raw, dict):
        unknown = sorted(set(raw) - {"text", "tool_calls", "status", "delay", "echo"})
        if unknown:
            raise ValueError(
                f"fake LLM script turn has {unknown}; a turn takes text, tool_calls, status, "
                "delay, echo"
            )
        return dict(raw)
    raise ValueError(f"fake LLM script turn {raw!r}: a string or a mapping")


def _status_error(status: int) -> Exception:
    import httpx
    import openai

    classes = {
        400: openai.BadRequestError,
        401: openai.AuthenticationError,
        403: openai.PermissionDeniedError,
        404: openai.NotFoundError,
        409: openai.ConflictError,
        422: openai.UnprocessableEntityError,
        429: openai.RateLimitError,
    }
    cls = classes.get(status) or (
        openai.InternalServerError if status >= 500 else openai.APIStatusError
    )
    request = httpx.Request("POST", "http://fake.invalid/v1/chat/completions")
    response = httpx.Response(status, request=request, json={"error": {"message": "scripted"}})
    return cls(f"fake LLM: scripted status {status}", response=response, body=None)


class FakeLLM(BaseLLM):
    """Answers from ``config.script``, in order."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.script: List[Dict[str, Any]] = [_turn(t) for t in (config.script or ["ok"])]
        self.calls: List[List[Any]] = []
        self._next = 0

    def _take(self, messages: Any) -> Dict[str, Any]:
        self.calls.append(list(messages or []))
        turn = self.script[min(self._next, len(self.script) - 1)]
        self._next += 1
        if turn.get("echo") is not None:
            users = [m for m in messages or [] if m.get("role") == "user"]
            said = users[-1].get("content") if users else ""
            if not isinstance(said, str):  # content parts: their text
                said = " ".join(p.get("text", "") for p in said or [] if isinstance(p, dict))
            prefix = turn["echo"] if isinstance(turn["echo"], str) else ""
            turn = {**turn, "text": f"{prefix}{said}"}
        return turn

    async def _wait(self, turn: Dict[str, Any]) -> None:
        if turn.get("delay"):
            await asyncio.sleep(float(turn["delay"]))
        if turn.get("status") is not None:
            raise _status_error(int(turn["status"]))

    def _usage(self, messages: Any, text: str) -> Any:
        from openai.types import CompletionUsage

        prompt = sum(len(str(m.get("content") or "")) for m in messages or []) // 4 + 1
        done = len(text) // 4 + 1
        return CompletionUsage(
            prompt_tokens=prompt, completion_tokens=done, total_tokens=prompt + done
        )

    @staticmethod
    def _calls(turn: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {
                "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "name": call["name"],
                "arguments": json.dumps(call.get("args") or {}),
            }
            for call in turn.get("tool_calls") or []
        ]

    async def generate(self, messages, *args, **kwargs):  # noqa: ANN001 — BaseLLM's
        from openai.types.chat import ChatCompletion, ChatCompletionMessage
        from openai.types.chat.chat_completion import Choice
        from openai.types.chat.chat_completion_message_tool_call import (
            ChatCompletionMessageToolCall,
            Function,
        )

        turn = self._take(messages)
        await self._wait(turn)
        text = turn.get("text")
        calls = self._calls(turn)
        message = ChatCompletionMessage(
            role="assistant",
            content=text,
            tool_calls=[
                ChatCompletionMessageToolCall(
                    id=c["id"],
                    type="function",
                    function=Function(name=c["name"], arguments=c["arguments"]),
                )
                for c in calls
            ]
            or None,
        )
        return ChatCompletion(
            id=f"fake-{uuid.uuid4().hex[:12]}",
            object="chat.completion",
            created=int(time.time()),
            model=getattr(self.config, "model", "fake"),
            choices=[
                Choice(
                    index=0,
                    finish_reason="tool_calls" if calls else "stop",
                    message=message,
                )
            ],
            usage=self._usage(messages, text or ""),
        )

    async def stream(self, messages, *args, **kwargs) -> AsyncGenerator[Any, None]:  # noqa: ANN001
        from openai.types.chat import ChatCompletionChunk
        from openai.types.chat.chat_completion_chunk import (
            Choice,
            ChoiceDelta,
            ChoiceDeltaToolCall,
            ChoiceDeltaToolCallFunction,
        )

        turn = self._take(messages)
        await self._wait(turn)
        text = turn.get("text") or ""
        calls = self._calls(turn)
        chunk_id = f"fake-{uuid.uuid4().hex[:12]}"
        model = getattr(self.config, "model", "fake")
        size = max(int(getattr(self.config, "chunk_size", 4) or 4), 1)

        def chunk(delta: Any, finish: Any = None, usage: Any = None) -> Any:
            choices = [] if delta is None else [Choice(index=0, delta=delta, finish_reason=finish)]
            return ChatCompletionChunk(
                id=chunk_id,
                object="chat.completion.chunk",
                created=int(time.time()),
                model=model,
                choices=choices,
                usage=usage,
            )

        for start in range(0, len(text), size):
            yield chunk(ChoiceDelta(role="assistant", content=text[start : start + size]))
            await asyncio.sleep(0)
        if calls:
            yield chunk(
                ChoiceDelta(
                    role="assistant",
                    tool_calls=[
                        ChoiceDeltaToolCall(
                            index=i,
                            id=c["id"],
                            type="function",
                            function=ChoiceDeltaToolCallFunction(
                                name=c["name"], arguments=c["arguments"]
                            ),
                        )
                        for i, c in enumerate(calls)
                    ],
                )
            )
        yield chunk(ChoiceDelta(), finish="tool_calls" if calls else "stop")
        yield chunk(None, usage=self._usage(messages, text))
