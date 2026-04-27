"""LLMOp — language-model op for operonx-providers.

Uses ResourceHub to access LLM resources. Supports streaming, load balancing,
fallback chains, and OpenAI Batch API mode.
"""

import random
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from operonx.core import LOGGER
from operonx.core.configs import OpType
from operonx.core.media import Media
from operonx.core.ops import BaseOp
from operonx.core.ops.base import shorthand, split_shorthand_kwargs
from operonx.core.utils.common import Param
from operonx.providers.ops._utils import resolve_hub

if TYPE_CHECKING:
    from operonx.providers.llms.base import BaseLLM


def _mime_from_data_url(url: str) -> Optional[str]:
    """Extract the mime type from a ``data:image/png;base64,...`` header."""
    if not url.startswith("data:"):
        return None
    head = url[5:].split(",", 1)[0]
    if not head:
        return None
    return head.split(";", 1)[0] or None


class LLMOp(BaseOp):
    """Op that calls a language model via ResourceHub.

    Supports streaming, weighted load balancing across multiple models,
    fallback chains with retry, and OpenAI Batch API mode (50 % cheaper).

    Inputs:
        messages (list): Chat messages in OpenAI format. Required.
        temperature (float): Sampling temperature. Default: 0.0.
        max_tokens (int): Max output tokens. Default: None (model default).
        tools (list): Tool/function definitions. Default: None.
        tool_choice (str | dict): Tool selection strategy. Default: None.
        response_format (dict): Structured output format. Default: None.

    Outputs:
        content (str): Generated text.
        role (str): Message role (usually ``"assistant"``).
        finish_reason (str): Stop reason (``"stop"``, ``"tool_calls"``, etc.).
        model_used (str): Actual model that served the request.
        tool_calls (list): Tool-call objects (empty list when absent).
        usage (dict): Flat token-cost metrics with keys
            ``prompt_tokens``, ``completion_tokens``, ``total_tokens``,
            ``cached_tokens`` (cache hit), ``cache_write_tokens``
            (Anthropic cache write), ``reasoning_tokens``.
        extras (dict): Bag of uncommon fields — ``thinking_content``,
            ``refusal``, ``logprobs``. Values are ``None`` when absent.

    Example::

        llm = LLMOp.of(resource="gpt-4o", messages=PARENT["messages"])
    """

    __slots__ = [
        "resource",
        "batch_mode",
        "ratios",
        "_llms",
        "_batch_coordinator",
        "fallback",
        "_fallback_llms",
        "_rng",
        "_initialized",
    ]

    type: OpType = "llm"

    def __init__(
        self,
        resource: Optional[Union[str, List[str]]] = None,
        ratios: Optional[List[float]] = None,
        fallback: Optional[List[str]] = None,
        batch_mode: bool = False,
        seed: Optional[int] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs: Any,
    ):
        """Initialize LLMOp.

        Args:
            resource: Resource key(s) for LLM in ResourceHub.
                - Single string: "gpt-4"
                - List for load balancing: ["gpt-4", "claude-3"]
            ratios: Weight ratios for load balancing. Must sum to 1.0.
                Only used when resource is a list.
            fallback: Fallback resource key(s) to use when primary model fails.
                List of resource keys from ResourceHub, tried in order.
            batch_mode: Whether to use OpenAI Batch API (50% cheaper, async processing)
            seed: Optional seed for load balancing RNG. Provides reproducible selection.
            inputs: Input variable mappings
            outputs: Output variable mappings
            **kwargs: Additional keyword arguments for BaseOp
        """
        kwargs.setdefault("bound", "io")
        super().__init__(**kwargs)

        self.batch_mode = batch_mode
        self.contain_generation = True
        self.fallback = fallback
        self._rng = random.Random(seed)

        # Validate resource + ratios
        if isinstance(resource, list):
            self.resource = resource
            self.ratios = ratios or [1.0 / len(resource)] * len(resource)
            if len(self.ratios) != len(self.resource):
                raise ValueError(
                    f"ratios length ({len(self.ratios)}) must match "
                    f"resource length ({len(self.resource)})"
                )
            if abs(sum(self.ratios) - 1.0) > 0.01:
                raise ValueError(f"ratios must sum to 1.0, got {sum(self.ratios)}")
        else:
            self.resource = resource
            self.ratios = [1.0] if resource else None

        # I/O schema
        input_schema = {
            "messages": Param(type=list, required=True),
            "temperature": Param(type=float, default=0.0),
            "max_tokens": Param(type=int, default=None),
            "tools": Param(type=list, default=None),
            "tool_choice": Param(type=(str, dict), default=None),
            "response_format": Param(type=dict, default=None),
            "top_p": Param(type=float, default=None),
            "stop": Param(type=(str, list), default=None),
            "frequency_penalty": Param(type=float, default=None),
            "presence_penalty": Param(type=float, default=None),
            "seed": Param(type=int, default=None),
            "logprobs": Param(type=bool, default=None),
            "top_logprobs": Param(type=int, default=None),
            "n": Param(type=int, default=None),
            "user": Param(type=str, default=None),
        }

        output_schema = {
            "role": Param(type=str, default="assistant"),
            "content": Param(type=str, required=True),
            "finish_reason": Param(type=str, default=None),
            "model_used": Param(type=str, required=True),
            "tool_calls": Param(type=list, default=[]),
            "usage": Param(type=dict, default={}),
            "extras": Param(type=dict, default={}),
        }

        normalized_inputs = self._normalize_params(inputs)
        normalized_outputs = self._normalize_params(outputs)
        self.inputs = self._merge_params(input_schema, normalized_inputs)
        self.outputs = self._merge_params(output_schema, normalized_outputs)

        # Lazy-initialized from ResourceHub on first use
        self._llms: List["BaseLLM"] = []
        self._fallback_llms: List["BaseLLM"] = []
        self._batch_coordinator = None
        self._initialized = False

        # Core: stream → _stream_core, else → _generate_core
        if self.stream:
            self._set_core(self._stream_core)
        else:
            self._set_core(self._generate_core)

    # =========================================================================
    # Lazy init
    # =========================================================================

    def warmup(self) -> None:
        """Eagerly initialize LLM backends on engine startup."""
        self._ensure_initialized()

    def _ensure_initialized(self):
        """Lazy-init LLM backends from ResourceHub on first use."""
        if self._initialized:
            return
        hub = resolve_hub()

        if isinstance(self.resource, list):
            self._llms = [hub.get(f"llm:{key}") for key in self.resource]
        else:
            llm = hub.get(f"llm:{self.resource}")
            self._llms = [llm] if llm else []

        if self.fallback:
            self._fallback_llms = [hub.get(f"llm:{key}") for key in self.fallback]

        if self.batch_mode and self._llms:
            from operonx.providers.llms.batch_coordinator import BatchCoordinator

            config = self._llms[0].config
            batch_kwargs = {}
            if hasattr(config, "batch_size"):
                batch_kwargs["max_batch_size"] = config.batch_size
            if hasattr(config, "batch_flush_interval"):
                batch_kwargs["flush_interval"] = config.batch_flush_interval
            if hasattr(config, "batch_poll_interval"):
                batch_kwargs["poll_interval"] = config.batch_poll_interval
            if hasattr(config, "batch_timeout"):
                batch_kwargs["timeout"] = config.batch_timeout

            self._batch_coordinator = BatchCoordinator.get_coordinator(
                resource=self.resource if isinstance(self.resource, str) else self.resource[0],
                llm=self._llms[0],
                **batch_kwargs,
            )

        self._initialized = True

    # =========================================================================
    # LLM selection
    # =========================================================================

    def _select_llm(self) -> "BaseLLM":
        """Select an LLM: single model returns it, multi uses weighted random."""
        self._ensure_initialized()
        if len(self._llms) == 1:
            return self._llms[0]
        return self._rng.choices(self._llms, weights=self.ratios, k=1)[0]

    def _get_resource_key(self, llm: "BaseLLM") -> str:
        """Get the resource key string for a selected LLM."""
        if isinstance(self.resource, list):
            return self.resource[self._llms.index(llm)]
        return self.resource

    # =========================================================================
    # Core: generate (non-streaming)
    # =========================================================================

    async def _generate_core(self, **kwargs):
        """Select LLM → generate → fallback on error. Returns output dict."""
        llm_params = self._build_llm_params(kwargs)

        if self.batch_mode:
            self._ensure_initialized()
            if not self._batch_coordinator:
                raise RuntimeError("Batch coordinator not initialized")
            completion = await self._batch_coordinator.submit(**llm_params)
            return self._extract_completion(completion, self.resource)

        selected = self._select_llm()
        resource = self._get_resource_key(selected)

        try:
            completion = await selected.generate(**llm_params)
            return self._extract_completion(completion, resource)
        except Exception as e:
            if not self._fallback_llms:
                raise
            LOGGER.error(f"Primary {resource} failed: {e}")
            return await self._fallback_generate(llm_params)

    async def _fallback_generate(self, llm_params):
        """Try fallback LLMs in order. Raises if all fail."""
        for idx, fallback_llm in enumerate(self._fallback_llms):
            fallback_key = self.fallback[idx]
            try:
                LOGGER.info(f"Trying fallback {fallback_key}...")
                completion = await fallback_llm.generate(**llm_params)
                LOGGER.info(f"Fallback to {fallback_key} succeeded")
                return self._extract_completion(completion, fallback_key)
            except Exception as fallback_error:
                LOGGER.error(f"Fallback {fallback_key} failed: {fallback_error}")
        raise RuntimeError("All fallback models failed")

    # =========================================================================
    # Core: stream
    # =========================================================================

    async def _stream_core(self, **kwargs):
        """Select LLM → stream → fallback on error. Yields per-token dicts."""
        llm_params = self._build_llm_params(kwargs)
        selected = self._select_llm()
        resource = self._get_resource_key(selected)
        acc = self._new_stream_acc()

        try:
            async for chunk in selected.stream(**llm_params):
                yield_dict = self._process_chunk(chunk, acc)
                if yield_dict:
                    yield yield_dict
        except Exception as e:
            if not self._fallback_llms:
                raise
            LOGGER.error(f"Streaming from {resource} failed: {e}")
            async for result in self._fallback_stream(llm_params):
                if isinstance(result, dict) and "finish_reason" in result:
                    yield result
                    return
                yield result
            return

        yield self._stream_final(acc, resource)

    async def _fallback_stream(self, llm_params):
        """Try fallback LLMs for streaming. Yields chunks, final yield has metadata."""
        for idx, fallback_llm in enumerate(self._fallback_llms):
            fallback_key = self.fallback[idx]
            LOGGER.info(f"Trying streaming fallback {fallback_key}...")
            try:
                acc = self._new_stream_acc()
                async for chunk in fallback_llm.stream(**llm_params):
                    yield_dict = self._process_chunk(chunk, acc)
                    if yield_dict:
                        yield yield_dict
                LOGGER.info(f"Streaming fallback to {fallback_key} succeeded")
                yield self._stream_final(acc, fallback_key)
                return
            except Exception as fallback_error:
                LOGGER.error(f"Streaming fallback {fallback_key} failed: {fallback_error}")
        raise RuntimeError("All streaming fallback models failed")

    def _stream_final(self, acc, resource):
        """Build the final metadata yield for a stream."""
        return {
            "role": "assistant",
            "content": acc["response"],
            "finish_reason": acc["finish_reason"],
            "model_used": resource,
            "tool_calls": acc["tool_calls"],
            "usage": self._normalize_usage(acc["usage_raw"]),
            "extras": self._build_extras(
                thinking_content=acc["thinking_content"] or None,
                refusal=acc["refusal"],
                logprobs=None,
            ),
        }

    # =========================================================================
    # Helpers
    # =========================================================================

    def _build_llm_params(self, _inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Extract LLM-relevant params from inputs, dropping None values."""
        llm_param_keys = [
            "messages",
            "temperature",
            "max_tokens",
            "tools",
            "tool_choice",
            "response_format",
            "top_p",
            "stop",
            "frequency_penalty",
            "presence_penalty",
            "seed",
            "logprobs",
            "top_logprobs",
            "n",
            "user",
        ]
        return {k: v for k in llm_param_keys if (v := _inputs.get(k)) is not None}

    def _extract_completion(self, completion: Any, resource: str) -> Dict[str, Any]:
        """Extract structured output dict from a ChatCompletion response."""
        message = completion.choices[0].message
        choice = completion.choices[0]

        thinking_content = ""
        if hasattr(message, "reasoning_content"):
            thinking_content = message.reasoning_content or ""

        usage_raw = completion.usage.model_dump() if completion.usage else {}

        tool_calls = []
        if message.tool_calls:
            tool_calls = [tc.model_dump() for tc in message.tool_calls]

        refusal = getattr(message, "refusal", None)

        logprobs_data = None
        if hasattr(choice, "logprobs") and choice.logprobs:
            logprobs_data = (
                choice.logprobs.model_dump()
                if hasattr(choice.logprobs, "model_dump")
                else choice.logprobs
            )

        return {
            "role": "assistant",
            "content": message.content or "",
            "finish_reason": choice.finish_reason,
            "model_used": resource or completion.model,
            "tool_calls": tool_calls,
            "usage": self._normalize_usage(usage_raw),
            "extras": self._build_extras(
                thinking_content=thinking_content or None,
                refusal=refusal,
                logprobs=logprobs_data,
            ),
        }

    # =========================================================================
    # Trace-time media normalization (BaseOp hook)
    # =========================================================================

    def normalize_trace_io(self, inputs: Dict[str, Any], outputs: Dict[str, Any]) -> tuple:
        """Wrap OpenAI chat-format multimodal blocks as ``Media`` for tracing.

        Runs inside the tracing collector — returns a shallow-copied inputs
        dict with ``messages`` rewritten so ``image_url`` / ``input_audio``
        blocks become ``Media`` instances. Real state is untouched; this is
        only the trace-time view.
        """
        msgs = inputs.get("messages")
        if msgs:
            wrapped = self._wrap_openai_media_blocks(msgs)
            if wrapped is not msgs:
                inputs = {**inputs, "messages": wrapped}
        return inputs, outputs

    @staticmethod
    def _wrap_openai_media_blocks(messages: Any) -> Any:
        """Walk messages and convert multimodal blocks to ``Media`` wrappers.

        Recognizes:
          - ``{"type": "image_url", "image_url": {"url": "data:image/..."}}``
            → ``Media(data_url, mime_from_header)``
          - ``{"type": "input_audio", "input_audio": {"data": "...", "format": "wav"}}``
            → ``Media(b64_data, "audio/<format>")``

        Returns a new list when any wrapping happened, else the original
        reference so the collector can detect "no change" cheaply.
        """
        if not isinstance(messages, list):
            return messages

        changed = False
        new_msgs = []
        for msg in messages:
            if not isinstance(msg, dict):
                new_msgs.append(msg)
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                new_msgs.append(msg)
                continue

            new_content = []
            msg_changed = False
            for block in content:
                wrapped = LLMOp._wrap_media_block(block)
                if wrapped is not block:
                    msg_changed = True
                new_content.append(wrapped)
            if msg_changed:
                new_msgs.append({**msg, "content": new_content})
                changed = True
            else:
                new_msgs.append(msg)
        return new_msgs if changed else messages

    @staticmethod
    def _wrap_media_block(block: Any) -> Any:
        """Return a Media-wrapped copy of a multimodal block, or the original."""
        if not isinstance(block, dict):
            return block
        btype = block.get("type")

        if btype == "image_url":
            url = (block.get("image_url") or {}).get("url")
            if isinstance(url, str) and url.startswith("data:"):
                mime = _mime_from_data_url(url) or "image/*"
                return {
                    **block,
                    "image_url": {
                        **block["image_url"],
                        "url": Media(data=url, mime_type=mime),
                    },
                }

        if btype == "input_audio":
            audio = block.get("input_audio") or {}
            data = audio.get("data")
            fmt = audio.get("format") or "wav"
            if isinstance(data, str):
                return {
                    **block,
                    "input_audio": {
                        **audio,
                        "data": Media(data=data, mime_type=f"audio/{fmt}"),
                    },
                }
        return block

    @staticmethod
    def _normalize_usage(raw: Dict[str, Any]) -> Dict[str, int]:
        """Flatten a Pydantic CompletionUsage dump into named cost metrics.

        Works for OpenAI/Azure/Gemini (cached_tokens nested under
        prompt_tokens_details) and for Anthropic (cache_write_tokens
        stashed as a Pydantic model_extra on usage).
        """
        if not raw:
            return {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "reasoning_tokens": 0,
            }
        prompt_details = raw.get("prompt_tokens_details") or {}
        completion_details = raw.get("completion_tokens_details") or {}
        return {
            "prompt_tokens": raw.get("prompt_tokens") or 0,
            "completion_tokens": raw.get("completion_tokens") or 0,
            "total_tokens": raw.get("total_tokens") or 0,
            "cached_tokens": (prompt_details.get("cached_tokens") if prompt_details else 0) or 0,
            "cache_write_tokens": raw.get("cache_write_tokens") or 0,
            "reasoning_tokens": (
                (completion_details.get("reasoning_tokens") if completion_details else 0) or 0
            ),
        }

    @staticmethod
    def _build_extras(
        *, thinking_content: Optional[str], refusal: Optional[str], logprobs: Any
    ) -> Dict[str, Any]:
        """Build the extras bag — always three keys, null when absent."""
        return {
            "thinking_content": thinking_content,
            "refusal": refusal,
            "logprobs": logprobs,
        }

    @staticmethod
    def _process_chunk(chunk, acc: dict) -> Optional[dict]:
        """Process a single stream chunk, updating accumulator state."""
        if chunk.usage:
            acc["usage_raw"] = chunk.usage.model_dump()

        if not chunk.choices:
            return None

        choice = chunk.choices[0]

        if hasattr(choice.delta, "reasoning_content") and choice.delta.reasoning_content:
            acc["thinking_content"] += choice.delta.reasoning_content

        if choice.delta.content:
            acc["response"] += choice.delta.content
            yield_dict = {"content": choice.delta.content, "role": "assistant"}
        else:
            yield_dict = None

        if choice.finish_reason:
            acc["finish_reason"] = choice.finish_reason

        if choice.delta.tool_calls:
            acc["tool_calls"].extend([tc.model_dump() for tc in choice.delta.tool_calls])

        if hasattr(choice.delta, "refusal") and choice.delta.refusal:
            acc["refusal"] = (acc["refusal"] or "") + choice.delta.refusal

        return yield_dict

    @staticmethod
    def _new_stream_acc() -> dict:
        """Create a fresh accumulator for streaming state."""
        return {
            "response": "",
            "thinking_content": "",
            "finish_reason": "stop",
            "usage_raw": {},
            "tool_calls": [],
            "refusal": None,
        }

    # =========================================================================
    # Shorthand factory
    # =========================================================================

    @shorthand
    def of(
        cls, resource=None, *, ratios=None, fallback=None, batch_mode=False, seed=None, **kwargs
    ) -> "LLMOp":
        """Create an LLMOp with flat kwargs.

        Example::

            llm = LLMOp.of(resource="gpt-4", messages=PARENT["messages"], outputs={"*": PARENT})
            llm = LLMOp.of(resource=["gpt-4", "claude-3"], ratios=[0.7, 0.3], messages=PARENT["messages"])
        """
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        return cls(
            resource=resource,
            ratios=ratios,
            fallback=fallback,
            batch_mode=batch_mode,
            seed=seed,
            inputs=input_mappings or None,
            **init_kwargs,
        )

    # =========================================================================
    # Serialization
    # =========================================================================

    def serialize(self) -> dict:
        """Serialize LLMOp for Rust backend, including backend configs."""
        self._ensure_initialized()
        base = super().serialize()

        base["resource"] = self.resource
        base["ratios"] = self.ratios
        base["fallback"] = self.fallback
        base["batch_mode"] = self.batch_mode

        configs = []
        for llm in self._llms:
            if llm and hasattr(llm, "config"):
                configs.append(llm.config.model_dump(mode="json"))
        if configs:
            base["resource_configs"] = configs

        fallback_configs = []
        for llm in self._fallback_llms:
            if llm and hasattr(llm, "config"):
                fallback_configs.append(llm.config.model_dump(mode="json"))
        if fallback_configs:
            base["fallback_configs"] = fallback_configs

        return base

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return LLM-specific metadata dictionary."""
        metadata = {
            "model": self.resource,
            "batch_mode": self.batch_mode,
        }
        if isinstance(self.resource, list):
            metadata["load_balancing"] = True
            metadata["ratios"] = self.ratios
        if self.fallback:
            metadata["fallback"] = self.fallback
        return metadata
