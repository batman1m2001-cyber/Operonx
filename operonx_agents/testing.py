"""Scripted models for tests: an agent's run, offline and the same every time.

``ScriptedLLM`` speaks the backend interface :class:`~operonx_agents.Model`
uses (``generate`` returning an OpenAI ``ChatCompletion``, ``stream``
yielding chunks), built from the SDK's own types so the shapes are the
real ones. Each script item is a reply, an exception to raise, or a
callable ``(messages, params) -> reply``; the last item repeats. ``says``
and ``asks`` build replies; ``scripted(name=backend)`` serves backends as
``llm:<name>`` resources for the length of a ``with`` block::

    from operonx_agents.testing import ScriptedLLM, asks, says, scripted

    with scripted(assistant=ScriptedLLM(asks(("add", {"a": 2, "b": 3})), says("5"))):
        res = await Runner.run(Agent(name="calc", model=Model("assistant"), tools=[add]), "2+3?")
    assert res.output == "5"
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional

from openai.types.chat import ChatCompletion, ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import Choice as ChunkChoice
from openai.types.chat.chat_completion_chunk import ChoiceDelta

__all__ = [
    "FakeHub",
    "ScriptedLLM",
    "asks",
    "calls",
    "chunk",
    "chunks_of",
    "completion",
    "says",
    "scripted",
]


def completion(
    content: str = "",
    *,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 3,
    logprobs: Optional[List[tuple]] = None,
    refusal: Optional[str] = None,
    reasoning: Optional[str] = None,
) -> ChatCompletion:
    """A completion as the SDK builds one from a live body: constructed,
    not validated, since gateways send values outside the SDK's Literals
    (Gemini's ``finish_reason: "safety"``)."""
    calls = [
        {
            "id": c.get("id", f"call_{i}"),
            "type": "function",
            "function": {
                "name": c["name"],
                "arguments": c.get("raw") or json.dumps(c.get("args", {})),
            },
        }
        for i, c in enumerate(tool_calls or [])
    ]
    lp = None
    if logprobs:
        lp = {
            "content": [
                {"token": t, "logprob": p, "bytes": None, "top_logprobs": []} for t, p in logprobs
            ]
        }
    body = {
        "id": "cmpl",
        "created": 0,
        "model": "m",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": calls or None,
                    "refusal": refusal,
                    **({"reasoning_content": reasoning} if reasoning else {}),
                },
                "finish_reason": finish_reason,
                "logprobs": lp,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
    return ChatCompletion.construct(**body)


def chunk(
    content: Optional[str] = None, finish_reason: Optional[str] = None
) -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id="c",
        created=0,
        model="m",
        object="chat.completion.chunk",
        choices=[
            ChunkChoice(index=0, delta=ChoiceDelta(content=content), finish_reason=finish_reason)
        ],
    )


def chunks_of(reply: ChatCompletion, pieces: int = 2) -> List[ChatCompletionChunk]:
    """A completion as a gateway streams it: the text in ``pieces`` parts,
    reasoning first (``reasoning_content``, as Qwen sends it), each tool
    call as one delta, the stop reason, then a usage-only chunk."""
    choice = reply.choices[0]
    message = choice.message
    out: List[ChatCompletionChunk] = []

    def piece(**delta: Any) -> ChatCompletionChunk:
        return ChatCompletionChunk.construct(
            id="c",
            created=0,
            model="m",
            object="chat.completion.chunk",
            choices=[ChunkChoice.construct(index=0, delta=ChoiceDelta.construct(**delta))],
        )

    thought = getattr(message, "reasoning_content", None)
    if thought:
        out.append(piece(reasoning_content=thought))
    text = message.content or ""
    if text:
        step = max(1, -(-len(text) // pieces))
        out.extend(piece(content=text[i : i + step]) for i in range(0, len(text), step))
    for index, call in enumerate(message.tool_calls or ()):
        data = call if isinstance(call, dict) else call.model_dump()
        out.append(piece(tool_calls=[{**data, "index": index}]))
    out.append(
        ChatCompletionChunk.construct(
            id="c",
            created=0,
            model="m",
            object="chat.completion.chunk",
            choices=[
                ChunkChoice.construct(
                    index=0,
                    delta=ChoiceDelta.construct(),
                    finish_reason=choice.finish_reason,
                    logprobs=choice.logprobs,
                )
            ],
        )
    )
    out.append(
        ChatCompletionChunk.construct(
            id="c",
            created=0,
            model="m",
            object="chat.completion.chunk",
            choices=[],
            usage=reply.usage,
        )
    )
    return out


class ScriptedLLM:
    """A backend that answers from a script, recording every request."""

    def __init__(
        self,
        *script: Any,
        delay: float = 0.0,
        structured_output: str = "prompted",
        stream_script: Optional[List[Any]] = None,
        cost: Optional[tuple] = None,
        max_retries: int = 0,
    ) -> None:
        self.script = list(script)
        self.stream_script = list(stream_script or [])
        self.delay = delay
        self.requests: List[Dict[str, Any]] = []
        self.calls = 0
        self.config = SimpleNamespace(
            structured_output=structured_output,
            cost_per_input_token=cost[0] if cost else None,
            cost_per_output_token=cost[1] if cost else None,
            max_retries=max_retries,
            retry_base_delay=0.0,
            retry_min_delay=0.0,
            retry_max_delay=0.0,
            generation_extras=None,
            model="scripted",
        )

    async def generate(self, messages, **params) -> ChatCompletion:
        self.calls += 1
        self.requests.append({"messages": list(messages), **params, "t": time.perf_counter()})
        if self.delay:
            await asyncio.sleep(self.delay)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if callable(item) and not isinstance(item, ChatCompletion):
            item = item(messages, params)
        if isinstance(item, BaseException):
            raise item
        return item

    async def stream(self, messages, **params):
        """``stream_script`` when given; else the next ``script`` item,
        streamed: what an agent's runner calls."""
        self.calls += 1
        self.requests.append({"messages": list(messages), **params, "t": time.perf_counter()})
        if self.stream_script:
            items = self.stream_script
        else:
            if self.delay:
                await asyncio.sleep(self.delay)
            item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
            if callable(item) and not isinstance(item, ChatCompletion):
                item = item(messages, params)
            items = [item] if isinstance(item, BaseException) else chunks_of(item)
        for item in items:
            if isinstance(item, BaseException):
                raise item
            await asyncio.sleep(0)
            yield item


class FakeHub:
    """Stands in for ``ResourceHub.instance()``: ``get("llm:x")``."""

    def __init__(self, **llms: Any) -> None:
        self.llms = llms

    def get(self, key: str) -> Any:
        category, _, name = key.partition(":")
        assert category == "llm", key
        if name not in self.llms:
            raise KeyError(f"Resource '{key}' not found")
        return self.llms[name]


def calls(*specs: Any, turn: int = 0) -> List[Dict[str, Any]]:
    """``calls(("add", {"a": 1}), ...)``: tool calls with ids ``t<turn>_<i>``."""
    return [{"id": f"t{turn}_{i}", "name": n, "args": a} for i, (n, a) in enumerate(specs)]


def asks(*specs: Any, turn: int = 0, **kw: Any) -> ChatCompletion:
    """A reply that calls tools: ``asks(("add", {"a": 1, "b": 2}))``."""
    kw.setdefault("finish_reason", "tool_calls")
    return completion("", tool_calls=calls(*specs, turn=turn), **kw)


def says(text: str = "final", **kw: Any) -> ChatCompletion:
    """A reply that answers with ``text``."""
    return completion(text, **kw)


@contextmanager
def scripted(**llms: Any) -> Iterator[FakeHub]:
    """Serve ``llms`` as ``llm:<name>`` resources inside the block: the
    process's ``ResourceHub`` instance, put back as it was after."""
    from operonx.core.registry.resource_hub import ResourceHub

    try:
        before = ResourceHub.instance()
    except RuntimeError:  # none installed yet
        before = None
    hub = FakeHub(**llms)
    ResourceHub.set_instance(hub)
    try:
        yield hub
    finally:
        if before is None:
            ResourceHub.reset_instance()
        else:
            ResourceHub.set_instance(before)
