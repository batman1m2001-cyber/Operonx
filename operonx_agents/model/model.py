"""``Model`` — one LLM, its fallbacks, a deadline over all of them, real usage.

A thin object over ``llm:`` resources (``resources.yaml``, read through
operonx's ``ResourceHub``)::

    fast = Model("inhouse", fallback=["qwen3.7-plus"], deadline=0.9,
                 settings=ModelSettings(temperature=0, max_tokens=64, logprobs=True))
    reply = await fast.request(messages)           # ModelResponse
    async for piece in fast.stream(messages): ...  # str deltas, then a ModelResponse

Who retries what (operonx.agents' rule, kept):

- **Transport** — 429, 5xx, a dropped connection, the resource's own
  ``timeout:`` — retried on the *same* resource as its ``max_retries`` /
  ``retry_*`` say, the way ``LLMOp`` does.
- **Fallback** — a resource that still fails, or refuses
  (``finish_reason`` ``content_filter``/``safety``, or a ``refusal``),
  hands over to the next one. A stream falls back only before its first
  delta: after that the consumer has acted on the text (a voice app has
  spoken it), so the error propagates.
- **Deadline** — ``deadline=`` seconds over the whole chain, every retry
  and fallback included; past it the call is cancelled and
  :class:`~operonx_agents.ModelTimeout` raised.
- **Output** — an answer that does not validate is re-asked by the output
  layer (:mod:`operonx_agents.model.output`), inside the same deadline.

Each resource tried by :meth:`Model.request` or :meth:`Model.stream` is
recorded as a child execution (``op_type="llm"``) of the op running it,
with the messages, the answer and ``gen_ai.*`` attributes. A stream's
record stays open across its yields without becoming the current frame
(``child(current=False)``), so the consumer's own steps stay its siblings.
"""

from __future__ import annotations

import asyncio
import random
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Union

from operonx import child
from operonx.core import LOGGER
from operonx.core.registry.resource_hub import ResourceHub
from operonx.providers.llms.base import normalize_tool_call

from operonx_agents.errors import ModelError, ModelRefused, ModelTimeout
from operonx_agents.model._deadline import deadline as _deadline
from operonx_agents.model.usage import Usage

__all__ = ["Model", "ModelResponse", "ModelSettings", "Reasoning"]

#: Stop reasons that mean the provider declined to answer.
REFUSAL_REASONS = frozenset({"content_filter", "safety"})


@dataclass(frozen=True)
class ModelSettings:
    """Request knobs. ``None`` leaves a knob to the resource and backend
    defaults; ``extra`` is sent as given (vendor parameters)."""

    temperature: Optional[float] = 0.0
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    seed: Optional[int] = None
    logprobs: bool = False
    top_logprobs: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def merge(self, other: Optional["ModelSettings"]) -> "ModelSettings":
        """``other``'s set fields over these (a per-call override)."""
        if other is None:
            return self
        changes = {
            name: getattr(other, name)
            for name in ("temperature", "max_tokens", "top_p", "seed", "top_logprobs")
            if getattr(other, name) is not None
        }
        if other.logprobs:
            changes["logprobs"] = True
        if other.extra:
            changes["extra"] = {**self.extra, **other.extra}
        return replace(self, **changes)

    def params(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            k: v
            for k, v in (
                ("temperature", self.temperature),
                ("max_tokens", self.max_tokens),
                ("top_p", self.top_p),
                ("seed", self.seed),
            )
            if v is not None
        }
        if self.logprobs:
            out["logprobs"] = True
            if self.top_logprobs is not None:
                out["top_logprobs"] = self.top_logprobs
        out.update(self.extra)
        return out


@dataclass(frozen=True)
class Reasoning:
    """A piece of the model's thinking, streamed apart from the answer
    (``stream(reasoning=True)``). Not a ``str``, so code that joins the
    ``str`` pieces into the answer never mixes it in."""

    text: str


@dataclass(frozen=True)
class ModelResponse:
    """One answer.

    Attributes:
        content: The text ("" when the model only called tools).
        tool_calls: ``{"id", "name", "args"}`` per call.
        finish_reason: The provider's stop reason.
        usage: This answer's tokens and cost — every request it took,
            retries and failed fallbacks included.
        model_used: The resource that answered.
        logprobs: One dict per output token (``token``, ``logprob``,
            ``top_logprobs``) when asked for and returned, else ``None``.
    """

    content: str
    tool_calls: List[Dict[str, Any]]
    finish_reason: Optional[str]
    usage: Usage
    model_used: str
    logprobs: Optional[List[Dict[str, Any]]] = None


class Model:
    """An ``llm:`` resource, its fallbacks and a deadline.

    Args:
        resource: The ``resources.yaml`` key without ``llm:``.
        fallback: Resources tried in order when the one before fails or
            refuses.
        deadline: Seconds for a whole call, the chain and the output
            re-asks included. ``None``: no bound beyond each resource's
            ``timeout:``.
        settings: Default request knobs; a call may override them.
    """

    __slots__ = ("resource", "fallback", "deadline", "settings", "_llms", "_params")

    def __init__(
        self,
        resource: str,
        *,
        fallback: Sequence[str] = (),
        deadline: Optional[float] = None,
        settings: Optional[ModelSettings] = None,
    ) -> None:
        if not isinstance(resource, str) or not resource or resource.startswith("llm:"):
            raise ValueError(
                f"Model resource {resource!r}: give the resources.yaml key without 'llm:' "
                "(Model('inhouse') reads llm:inhouse)."
            )
        if deadline is not None and deadline <= 0:
            raise ValueError(f"Model deadline must be positive seconds, got {deadline!r}.")
        self.resource = resource
        self.fallback = tuple(fallback)
        self.deadline = deadline
        self.settings = settings or ModelSettings()
        self._llms: Dict[str, Any] = {}
        self._params = self.settings.params()

    @property
    def resources(self) -> List[str]:
        return [self.resource, *self.fallback]

    def llm(self, resource: Optional[str] = None) -> Any:
        """The backend for ``resource`` (default: the primary), from the
        ResourceHub. ``operonx.bootstrap()`` must have loaded it.

        Kept per hub: a model made once (a module-level agent) reads the
        hub installed now — a test's, or a re-bootstrapped one — not the
        first one it ever resolved against."""
        key = resource or self.resource
        hub = ResourceHub.instance()
        held = self._llms.get(key)
        if held is None or held[0] is not hub:
            held = self._llms[key] = (hub, hub.get(f"llm:{key}"))
        return held[1]

    def structured_output(self, resource: Optional[str] = None) -> str:
        """What the resource declares: ``native``, ``tool`` or ``prompted``."""
        return getattr(getattr(self.llm(resource), "config", None), "structured_output", "prompted")

    def __repr__(self) -> str:
        return f"Model({self.resource!r}, fallback={list(self.fallback)}, deadline={self.deadline})"

    def _request_params(self, settings, tools, tool_choice, response_format) -> Dict[str, Any]:
        params = dict(self._params) if settings is None else self.settings.merge(settings).params()
        if tools is not None:
            params["tools"] = tools
        if tool_choice is not None:
            params["tool_choice"] = tool_choice
        if response_format is not None:
            params["response_format"] = response_format
        return params

    # ── requests ───────────────────────────────────────────────────────

    async def request(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Any = None,
        response_format: Optional[Dict[str, Any]] = None,
        settings: Optional[ModelSettings] = None,
        redact: Any = None,
    ) -> ModelResponse:
        """One answer, through the fallback chain, within the deadline.
        ``redact``: as for :meth:`stream`.

        Raises:
            ModelTimeout: the deadline passed.
            ModelRefused: every resource refused.
            ModelError: every resource failed (``attempts`` says how).
        """
        async with self.bounded():
            return await self.request_unbounded(
                messages,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
                settings=settings,
                redact=redact,
            )

    def bounded(self) -> Any:
        """``async with model.bounded():`` — the model's deadline over a
        block of several requests (an answer and its re-asks), raising
        :class:`ModelTimeout`. Without a deadline, a no-op."""
        return _Bounded(self) if self.deadline is not None else nullcontext()

    async def request_unbounded(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Any = None,
        response_format: Optional[Dict[str, Any]] = None,
        settings: Optional[ModelSettings] = None,
        redact: Any = None,
    ) -> ModelResponse:
        """:meth:`request` without the deadline — for code already inside
        :meth:`bounded`."""
        params = self._request_params(settings, tools, tool_choice, response_format)
        attempts: List[tuple] = []
        spent = Usage()
        refused = 0
        recorded = {"messages": messages}
        for resource in self.resources:
            llm = self.llm(resource)
            try:
                async with child("model", inputs=recorded, op_type="llm") as rec:
                    rec.redact = redact  # set first: a failed call is exported too
                    completion, used = await _with_transport_retry(
                        llm, _per_resource(llm, params), messages
                    )
                    reply = _response(completion, resource, spent + used)
                    _record(rec, llm, resource, reply, used)
            except Exception as exc:  # noqa: BLE001 - the next resource decides
                attempts.append((resource, f"{type(exc).__name__}: {exc}"))
                LOGGER.warning("model %s failed (%s); %s", resource, exc, _next(self, resource))
                continue
            spent = reply.usage
            if _refused(completion, reply):
                refused += 1
                attempts.append((resource, f"refused (finish_reason={reply.finish_reason!r})"))
                LOGGER.warning("model %s refused; %s", resource, _next(self, resource))
                continue
            return reply
        cls = ModelRefused if refused == len(self.resources) else ModelError
        raise cls(
            f"every resource of {self!r} failed or refused: "
            + "; ".join(f"{r}: {why}" for r, why in attempts)
            + ". Check the gateways, or add a fallback= resource that answers.",
            attempts,
        )

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Any = None,
        response_format: Optional[Dict[str, Any]] = None,
        settings: Optional[ModelSettings] = None,
        reasoning: bool = False,
        redact: Any = None,
    ) -> AsyncIterator[Union[str, Reasoning, ModelResponse]]:
        """Text deltas (``str``) as they arrive, then the whole
        :class:`ModelResponse`. A resource is abandoned for the next only
        before it has yielded a delta.

        ``reasoning=True`` also yields the model's thinking, where the
        gateway streams it (``reasoning_content``), as :class:`Reasoning`
        pieces; it never enters the answer's text.

        ``redact`` (``dict -> dict``) scrubs each call's record where the
        trace leaves the process (operonx ``child()``'s ``redact``: the run
        stores and consumers apply it, not this loop); the model is sent,
        and answers, the real text.

        Consume it in the task that started it, and do not await other
        work between pieces: the deadline cancels the consuming task, and
        only a cancel that lands inside this generator becomes
        :class:`ModelTimeout`.
        """
        recorded = {"messages": messages}
        params = self._request_params(settings, tools, tool_choice, response_format)
        attempts: List[tuple] = []
        async with self.bounded():
            for resource in self.resources:
                llm = self.llm(resource)
                acc = _StreamAcc()
                emitted = False
                try:
                    async with child("model", inputs=recorded, op_type="llm", current=False) as rec:
                        rec.redact = redact
                        async for chunk in llm.stream(
                            messages=messages, **_per_resource(llm, params)
                        ):
                            delta, thought = acc.add(chunk)
                            if thought and reasoning:
                                yield Reasoning(thought)
                            if delta:
                                emitted = True
                                yield delta
                        reply = acc.response(resource, llm)
                        _record(rec, llm, resource, reply, reply.usage)
                except Exception as exc:  # noqa: BLE001
                    if emitted:
                        raise  # a replay would contradict what the consumer has
                    attempts.append((resource, f"{type(exc).__name__}: {exc}"))
                    LOGGER.warning("model %s failed (%s); %s", resource, exc, _next(self, resource))
                    continue
                if not emitted and reply.finish_reason in REFUSAL_REASONS:
                    attempts.append((resource, f"refused (finish_reason={reply.finish_reason!r})"))
                    continue
                yield reply
                return
        raise ModelError(
            f"every resource of {self!r} failed before streaming: "
            + "; ".join(f"{r}: {why}" for r, why in attempts),
            attempts,
        )


class _Bounded:
    """The model's deadline as an async context manager."""

    __slots__ = ("_model", "_cm")

    def __init__(self, model: Model) -> None:
        self._model = model
        self._cm = None

    async def __aenter__(self) -> None:
        self._cm = _deadline(self._model.deadline)
        await self._cm.__aenter__()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        try:
            return bool(await self._cm.__aexit__(exc_type, exc, tb))
        except TimeoutError:
            raise ModelTimeout(self._model.deadline, self._model.resource) from None


def _next(model: Model, resource: str) -> str:
    rest = model.resources[model.resources.index(resource) + 1 :]
    return f"trying {rest[0]}" if rest else "no fallback left"


def _per_resource(llm: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """A resource's ``generation_extras`` under the call's own params (the
    call wins; a ``None`` extra removes the key — ``LLMOp``'s rule)."""
    extras = getattr(getattr(llm, "config", None), "generation_extras", None)
    if not isinstance(extras, dict) or not extras:
        return params
    merged = dict(params)
    for key, value in extras.items():
        merged.setdefault(key, value)
    return merged


async def _with_transport_retry(llm: Any, params: Dict[str, Any], messages) -> tuple:
    """``(completion, usage of every attempt)``; retries what the resource
    config says is transient, on this resource only."""
    cfg = getattr(llm, "config", None)
    retries = _num(cfg, "max_retries", 0)
    base = _num(cfg, "retry_base_delay", 5.0)
    floor = _num(cfg, "retry_min_delay", 0.0)
    cap = _num(cfg, "retry_max_delay", 60.0)
    attempt = 0
    while True:
        try:
            completion = await llm.generate(messages=messages, **params)
            return completion, Usage.from_completion(getattr(completion, "usage", None), cfg)
        except Exception as exc:  # noqa: BLE001
            if attempt >= retries or not _transient(exc):
                raise
            delay = random.uniform(floor, max(floor, min(cap, base * 2**attempt)))
            LOGGER.warning(
                "transient %s (%s); retry %d/%d", type(exc).__name__, exc, attempt + 1, retries
            )
            attempt += 1
            await asyncio.sleep(delay)


def _num(cfg: Any, name: str, default: float) -> Any:
    value = getattr(cfg, name, None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return value


def _transient(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    if isinstance(status, int):
        return status == 429 or status >= 500
    name = type(exc).__name__
    return isinstance(exc, (ConnectionError, TimeoutError)) or name in (
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ReadTimeout",
        "ConnectTimeout",
        "RemoteProtocolError",
    )


def _response(completion: Any, resource: str, usage: Usage) -> ModelResponse:
    choice = completion.choices[0]
    message = choice.message
    tokens = getattr(getattr(choice, "logprobs", None), "content", None)
    return ModelResponse(
        content=_text(message.content),
        tool_calls=[normalize_tool_call(c) for c in message.tool_calls or ()],
        finish_reason=choice.finish_reason,
        usage=usage,
        model_used=resource,
        logprobs=[_dump(t) for t in tokens] if tokens else None,
    )


def _refused(completion: Any, reply: ModelResponse) -> bool:
    message = completion.choices[0].message
    return reply.finish_reason in REFUSAL_REASONS or bool(getattr(message, "refusal", None))


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "") for part in content if isinstance(part, dict)
    )  # content parts


def _field(value: Any, name: str) -> Any:
    """``value[name]`` of a dict, else the attribute (an SDK object)."""
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _dump(value: Any) -> Any:
    return value.model_dump() if hasattr(value, "model_dump") else value


def _record(rec: Any, llm: Any, resource: str, reply: ModelResponse, used: Usage) -> None:
    rec.outputs = {
        "content": reply.content,
        "tool_calls": reply.tool_calls,
        "finish_reason": reply.finish_reason,
        "usage": used.to_dict(),
        # The key the run store counts LLM calls by.
        "cost_usd": used.cost_usd,
    }
    rec.attrs.update(_gen_ai_attrs(llm, resource, reply, used))


def _gen_ai_attrs(llm: Any, resource: str, reply: ModelResponse, used: Usage) -> Dict[str, Any]:
    return {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": getattr(getattr(llm, "config", None), "model", resource),
        "gen_ai.response.finish_reasons": [reply.finish_reason],
        "gen_ai.usage.input_tokens": used.input_tokens,
        "gen_ai.usage.output_tokens": used.output_tokens,
        "operonx.resource": resource,
    }


class _StreamAcc:
    """A streamed answer, assembled: text, tool calls merged by index,
    logprobs, usage, stop reason."""

    def __init__(self) -> None:
        self.text: List[str] = []
        self.calls: Dict[int, Dict[str, Any]] = {}
        self.logprobs: List[Any] = []
        self.usage: Any = None
        self.finish_reason: Optional[str] = None

    def add(self, chunk: Any) -> tuple:
        """``(text delta, reasoning delta)`` of one chunk, either may be ""."""
        if getattr(chunk, "usage", None):
            self.usage = chunk.usage
        if not chunk.choices:
            return "", ""
        choice = chunk.choices[0]
        delta = choice.delta
        if choice.finish_reason:
            self.finish_reason = choice.finish_reason
        for part in getattr(delta, "tool_calls", None) or ():
            entry = self.calls.setdefault(
                _field(part, "index") or 0, {"id": "", "function": {"name": "", "arguments": ""}}
            )
            entry["id"] = entry["id"] or _field(part, "id") or ""
            fn = _field(part, "function")
            if fn is not None:
                entry["function"]["name"] = entry["function"]["name"] or _field(fn, "name") or ""
                entry["function"]["arguments"] += _field(fn, "arguments") or ""
        tokens = getattr(getattr(choice, "logprobs", None), "content", None)
        if tokens:
            self.logprobs.extend(_dump(t) for t in tokens)
        text = getattr(delta, "content", None) or ""
        if text:
            self.text.append(text)
        # A vendor field (Qwen, DeepSeek): read from the extras, so a delta
        # without it costs no AttributeError.
        extra = getattr(delta, "model_extra", None)
        return text, (extra.get("reasoning_content") if extra else None) or ""

    def response(self, resource: str, llm: Any) -> ModelResponse:
        return ModelResponse(
            content="".join(self.text),
            tool_calls=[normalize_tool_call(self.calls[i]) for i in sorted(self.calls)],
            finish_reason=self.finish_reason,
            usage=Usage.from_completion(self.usage, getattr(llm, "config", None)),
            model_used=resource,
            logprobs=self.logprobs or None,
        )
