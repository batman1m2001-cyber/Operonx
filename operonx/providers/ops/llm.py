"""LLMOp — unified prompt-formatting + language-model op for operonx-providers.

Accepts a polymorphic ``prompt=`` input (str / dict / list of messages) plus
template variables via ``**kwargs``. Formats messages internally, then calls
the LLM via ResourceHub. Supports streaming, load balancing, fallback chains,
and OpenAI Batch API mode.
"""

import random
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from operonx.core import LOGGER
from operonx.core.configs import OpType
from operonx.core.exceptions import PromptError
from operonx.core.media import Media
from operonx.core.ops import BaseOp
from operonx.core.ops.base import shorthand, split_shorthand_kwargs
from operonx.core.utils.common import Param
from operonx.providers.ops._utils import resolve_hub

if TYPE_CHECKING:
    from operonx.providers.llms.base import BaseLLM


# LLM knobs — never treated as template variables.
_LLM_PARAM_KEYS = (
    "prompt",
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
)
RESERVED_KEYS = frozenset(_LLM_PARAM_KEYS)


def _mime_from_data_url(url: str) -> Optional[str]:
    """Extract the mime type from a ``data:image/png;base64,...`` header."""
    if not url.startswith("data:"):
        return None
    head = url[5:].split(",", 1)[0]
    if not head:
        return None
    return head.split(";", 1)[0] or None


class LLMOp(BaseOp):
    """Op that formats a prompt and calls a language model via ResourceHub.

    The ``prompt=`` input accepts three shapes:

    * **str** — becomes a single user message: ``"Hello {name}"``.
    * **dict** with ``system`` / ``user`` keys — 1-2 messages with ``{var}``
      placeholders.
    * **list** — full OpenAI messages array (may contain multimodal blocks).

    All non-reserved kwargs are template variables — every string leaf inside
    ``prompt`` gets ``str.format_map``-substituted with them.

    Inputs:
        prompt (str | dict | list): Message template. Required.
        temperature (float): Sampling temperature. Default: 0.0.
        max_tokens (int): Max output tokens. Default: None.
        tools (list): Tool/function definitions. Default: None.
        tool_choice (str | dict): Tool selection strategy. Default: None.
        response_format (dict): Structured output format. Default: None.
        <var> (any): Template variables (``{var}`` placeholders).

    Outputs:
        content (str): Generated text.
        role (str): Message role (usually ``"assistant"``).
        finish_reason (str): Stop reason (``"stop"``, ``"tool_calls"``, ...).
        model_used (str): Actual model that served the request.
        tool_calls (list): Tool-call objects (empty list when absent).
        usage (dict): Flat token-cost metrics.
        extras (dict): Bag of uncommon fields (``thinking_content``, ``refusal``, ``logprobs``).

    Example::

        llm = LLMOp.of(
            resource="gpt-4o",
            prompt={"system": "You are {role}.", "user": "{query}"},
            role="helpful", query=PARENT["query"],
        )
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
            fallback: Fallback resource keys tried in order on failure.
            batch_mode: Use OpenAI Batch API (50% cheaper).
            seed: Optional seed for load balancing RNG.
            inputs: Input variable mappings.
            outputs: Output variable mappings.
            **kwargs: Additional keyword arguments for BaseOp.
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

        # Fixed LLM knobs
        input_schema = {
            "prompt": Param(type=(str, dict, list), required=True),
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

        # Wildcard PARENT inference: pull template var names from a static prompt
        if "__FORWARD_WILDCARD__" in normalized_inputs:
            for var in self._infer_wildcard_vars(normalized_inputs["__FORWARD_WILDCARD__"]):
                if var not in input_schema:
                    input_schema[var] = Param(type=Any, required=False, default=None)

        # Non-reserved user inputs are template variables
        for key in normalized_inputs:
            if key not in RESERVED_KEYS and key != "__FORWARD_WILDCARD__":
                if key not in input_schema:
                    input_schema[key] = Param(type=Any, required=False, default=None)

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
    # Prompt formatting (absorbed from PromptOp)
    # =========================================================================

    def _resolve_wildcard_source(self, wildcard_source):
        """Resolve PARENT sentinel to actual parent op."""
        if hasattr(wildcard_source, "name") and wildcard_source.name == "__PARENT__":
            return self.parent
        return wildcard_source

    def _infer_wildcard_vars(self, wildcard_source) -> set:
        """Infer template variable names from a wildcard source op.

        Strategy:
          1. If source has a static prompt → parse ``{var}`` placeholders.
          2. Otherwise (Ref or missing) → use source's non-reserved input keys.
        """
        from operonx.core.states.ref import Ref

        source = self._resolve_wildcard_source(wildcard_source)
        if source is None or not hasattr(source, "inputs"):
            return set()

        if "prompt" in source.inputs:
            param = source.inputs["prompt"]
            value = param.value if hasattr(param, "value") else param
            if value is not None and not isinstance(value, Ref):
                return _extract_template_variables(value) - RESERVED_KEYS

        return set(source.inputs) - RESERVED_KEYS

    def _build_messages(self, prompt: Any, vars: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Turn a prompt (str / dict / list) into an OpenAI messages array.

        Every string leaf inside ``prompt`` is ``str.format_map``-substituted
        with ``vars``. Raises ``PromptError`` on missing variable.
        """
        if isinstance(prompt, str):
            content = _format_value(prompt, vars, prompt)
            return [{"role": "user", "content": content}]

        if isinstance(prompt, dict):
            messages = []
            if "system" in prompt:
                messages.append(
                    {"role": "system", "content": _format_value(prompt["system"], vars, prompt)}
                )
            if "user" in prompt:
                messages.append(
                    {"role": "user", "content": _format_value(prompt["user"], vars, prompt)}
                )
            if messages:
                return messages
            raise PromptError(
                message="Prompt dict must contain 'system' and/or 'user' keys",
                prompt=prompt,
                original_error=ValueError(f"Got keys: {list(prompt)}"),
            )

        if isinstance(prompt, list):
            return [_format_value(msg, vars, prompt) for msg in prompt]

        raise PromptError(
            message="Invalid prompt type",
            prompt=prompt,
            original_error=TypeError(f"Expected str, dict, or list, got {type(prompt).__name__}"),
        )

    def _extract_prompt_and_vars(self, kwargs: Dict[str, Any]) -> tuple:
        """Split kwargs into (prompt, template_vars, llm_knobs)."""
        prompt = kwargs.pop("prompt", None)
        llm_knobs = {}
        for key in _LLM_PARAM_KEYS:
            if key == "prompt":
                continue
            if key in kwargs:
                val = kwargs.pop(key)
                if val is not None:
                    llm_knobs[key] = val
        # Anything left over is a template variable
        return prompt, kwargs, llm_knobs

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
        """Format prompt → select LLM → generate → fallback on error."""
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
        """Format prompt → select LLM → stream → fallback on error."""
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
        """Format prompt into messages and return LLM-ready params dict."""
        inputs_copy = dict(_inputs)
        prompt, template_vars, llm_knobs = self._extract_prompt_and_vars(inputs_copy)

        if prompt is None:
            raise PromptError(
                message="prompt is required",
                prompt=None,
                original_error=ValueError("LLMOp received prompt=None"),
            )

        messages = self._build_messages(prompt, template_vars)
        return {"messages": messages, **llm_knobs}

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

        Prompt is formatted to messages first (using the current template vars),
        then multimodal image/audio blocks are wrapped in ``Media`` for the
        trace-time view. Real op state is untouched.
        """
        prompt = inputs.get("prompt")
        if prompt is not None:
            try:
                vars = {k: v for k, v in inputs.items() if k not in RESERVED_KEYS}
                messages = self._build_messages(prompt, vars)
                wrapped = self._wrap_openai_media_blocks(messages)
                inputs = {**inputs, "messages": wrapped}
            except Exception:
                # Tracing must never break execution — swallow formatting errors
                pass
        return inputs, outputs

    @staticmethod
    def _wrap_openai_media_blocks(messages: Any) -> Any:
        """Walk messages and convert multimodal blocks to ``Media`` wrappers."""
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
        """Flatten a Pydantic CompletionUsage dump into named cost metrics."""
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
        cls,
        resource=None,
        *,
        ratios=None,
        fallback=None,
        batch_mode=False,
        seed=None,
        prompt=None,
        **kwargs,
    ) -> "LLMOp":
        """Create an LLMOp with flat kwargs.

        Example::

            llm = LLMOp.of(resource="gpt-4", prompt="Hello {name}", name="Alice")
            llm = LLMOp.of(
                resource=["gpt-4", "claude-3"],
                ratios=[0.7, 0.3],
                prompt={"system": "You are {role}.", "user": "{q}"},
                role="helper", q=PARENT["q"],
            )
        """
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        if prompt is not None:
            input_mappings["prompt"] = prompt
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
        """Return LLM + prompt metadata dictionary."""
        metadata = {
            "model": self.resource,
            "batch_mode": self.batch_mode,
        }
        if isinstance(self.resource, list):
            metadata["load_balancing"] = True
            metadata["ratios"] = self.ratios
        if self.fallback:
            metadata["fallback"] = self.fallback

        if "prompt" in self.inputs:
            val = self.inputs["prompt"].value
            if val is not None:
                # Ref → repr for pickle safety (background trace process)
                from operonx.core.states.ref import Ref

                metadata["prompt"] = repr(val) if isinstance(val, Ref) else val

        return metadata


# =============================================================================
# Module-level template helpers (kept out of the class to make them cheap to
# reuse from _infer_wildcard_vars without carrying self).
# =============================================================================


def _extract_template_variables(template: Any) -> set:
    """Find all ``{variable}`` names in a template (str, dict, or list)."""
    if isinstance(template, str):
        return set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", template))
    if isinstance(template, dict):
        result = set()
        for v in template.values():
            result |= _extract_template_variables(v)
        return result
    if isinstance(template, list):
        result = set()
        for item in template:
            result |= _extract_template_variables(item)
        return result
    return set()


def _format_value(value: Any, vars: Dict[str, Any], template: Any = None) -> Any:
    """Recursively format template variables in a value.

    Raises ``PromptError`` on missing var, with the full list of missing names.
    """
    if isinstance(value, str):
        try:
            return value.format_map(vars) if "{" in value else value
        except KeyError as e:
            required = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", value))
            missing = [v for v in required if v not in vars]
            raise PromptError(
                message="Missing template variable(s)",
                prompt=template if template is not None else value,
                missing_vars=missing,
                original_error=e,
            ) from e
    elif isinstance(value, dict):
        return {k: _format_value(v, vars, template) for k, v in value.items()}
    elif isinstance(value, list):
        return [_format_value(item, vars, template) for item in value]
    return value
