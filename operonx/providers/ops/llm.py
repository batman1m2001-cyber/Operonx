"""LLMOp — unified prompt-formatting + language-model op for operonx-providers.

Takes exactly one of ``prompt=`` (a template — str or dict — formatted with
template variables from ``**kwargs``) or ``messages=`` (a ready OpenAI
messages array, never formatted). Calls the LLM via ResourceHub. Supports
streaming, load balancing, fallback chains, and OpenAI Batch API mode.
"""

import random
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from operonx.core import LOGGER
from operonx.core.configs import OpType
from operonx.core.exceptions import LLMRefusalError, PromptError
from operonx.core.media import Media
from operonx.core.ops import BaseOp
from operonx.core.ops.base import shorthand, split_shorthand_kwargs
from operonx.core.utils.common import Param
from operonx.providers.ops._utils import resolve_hub
from operonx.providers.parsing import ExtractField, parse_and_extract

if TYPE_CHECKING:
    from operonx.providers.llms.base import BaseLLM


# LLM knobs — never treated as template variables.
_LLM_PARAM_KEYS = (
    "prompt",
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

    Give it exactly one of ``prompt=`` or ``messages=``.

    ``prompt=`` is a **template**, and every string in it is
    ``str.format_map``-substituted with the non-reserved kwargs:

    * **str** — one user message: ``"Hello {name}"``.
    * **dict** with ``system`` / ``user`` keys — the standard two-message call.

    ``messages=`` is a **conversation**: a full OpenAI messages array, passed
    through untouched. Use it whenever the content is data rather than a
    template — an agent's history, a multimodal block assembled upstream,
    anything past two messages.

    The split exists because formatting a conversation is destructive. Any
    brace in any message — a tool returning ``{"city": "Hanoi"}``, a user
    pasting CSS, the model's own tool-call arguments — becomes a template
    variable that does not exist, and the run dies on the *next* model call.
    ``prompt=`` used to accept a list, which made that the default outcome
    for every agent.

    Inputs:
        prompt (str | dict): Message template. Mutually exclusive with ``messages``.
        messages (list): Ready OpenAI messages array, never formatted.
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

        chat = LLMOp.of(resource="gpt-4o", messages=history["messages"])
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
        # Structured-output layer (merged from the deleted ParserOp in 1.0.0)
        "fields",
        "parser",
        "validators",
        "max_retries",
        "retry_hint",
        "_extract_fields",
    ]

    type: OpType = "llm"

    def __init__(
        self,
        resource: Optional[Union[str, List[str]]] = None,
        ratios: Optional[List[float]] = None,
        fallback: Optional[List[str]] = None,
        batch_mode: bool = False,
        seed: Optional[int] = None,
        # ── Structured-output layer (merged from ask()/ParserOp in 1.0.0) ───
        fields: Optional[List[str]] = None,
        parser: Optional[str] = None,
        validators: Optional[Dict[str, List[Any]]] = None,
        max_retries: int = 0,
        retry_hint: bool = True,
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
            fallback: Fallback resource keys tried in order on **hard** failures
                — refusals from the model, provider-side content filtering, or
                exhausted transport retries (the underlying SDK gave up). NOT
                triggered by parse/validator failures — those use ``max_retries``.
            batch_mode: Use OpenAI Batch API (50% cheaper).
            seed: Optional seed for load balancing RNG.
            fields: Optional list of ``"path.to.value: type"`` extraction schemas
                (see ``operonx.providers.parsing.ExtractField``). When set, the
                LLM response is parsed inline; each field becomes a top-level
                output of this op.
            parser: Parser format when ``fields`` is set: ``"xml"``, ``"json"``,
                or ``"yaml"``. Defaults to ``"xml"`` when ``fields`` is provided.
            validators: Optional per-field allow-list validators applied after
                extraction. Format: ``{"field_name": [allowed_value, ...]}``.
                A value prefixed with ``@`` in the list is used as a default
                when the extracted value doesn't match the allow-list.
            max_retries: Max **semantic** retries when the parser or validators
                report an error. Default 0 (no retry — first parse failure
                surfaces as ``error`` in the output). Transport errors are the
                SDK's responsibility and NOT counted here.
            retry_hint: When True (default) and retrying, append the previous
                LLM response and a "that failed — <error>, try again" user turn
                so the model sees what went wrong.
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

        # Structured-output config (merged from ParserOp).
        if fields and not parser:
            parser = "xml"
        if parser and not fields:
            raise TypeError(
                "LLMOp(parser=...) requires fields=[...] — parser has no work "
                "to do without extraction schemas."
            )
        if validators and not fields:
            raise TypeError(
                "LLMOp(validators=...) requires fields=[...] — nothing to "
                "validate without extracted values."
            )
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries}")
        self.fields = fields
        self.parser = parser
        self.validators = validators
        self.max_retries = max_retries
        self.retry_hint = retry_hint
        self._extract_fields = [ExtractField.from_string(s) for s in fields] if fields else None

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
            "prompt": Param(type=(str, dict), default=None),
            "messages": Param(type=list, default=None),
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
            # Streaming only: False on a token delta, True on the frame
            # that repeats the accumulated content. Batch calls are always
            # final. See ``_stream_final``.
            "final": Param(type=bool, default=True),
            "finish_reason": Param(type=str, default=None),
            "model_used": Param(type=str, required=True),
            "tool_calls": Param(type=list, default=[]),
            "usage": Param(type=dict, default={}),
            "extras": Param(type=dict, default={}),
        }
        # When fields=[...] is set, extend the output schema with one Param
        # per extracted field and an ``error`` field (parse/validate error
        # string, or None on success). Callers wire the individual fields
        # through refs the same way they used to wire ParserOp outputs.
        if self._extract_fields:
            for f in self._extract_fields:
                output_schema[f.output_key] = Param(default=None)
            output_schema["error"] = Param(type=str, default=None)

        # Checked on the *raw* mapping: _normalize_params wraps each value
        # in a Param, so a literal list is no longer a list by then.
        _check_prompt_inputs(inputs or {})

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
        """Turn a **template** (str / dict) into an OpenAI messages array.

        Every string leaf is ``str.format_map``-substituted with ``vars``.
        Raises ``PromptError`` on a missing variable.
        """
        if isinstance(prompt, list):
            raise PromptError(
                message=(
                    "prompt= no longer accepts a list. A list of messages is a "
                    "conversation, and formatting one is destructive: any brace "
                    "in any message becomes a template variable that does not "
                    "exist. Pass it as messages= instead, which is never "
                    "formatted. If you meant a multi-message *template*, build "
                    "the list in an upstream @op and pass the result as "
                    "messages=; prompt={'system': ..., 'user': ...} covers the "
                    "two-message case."
                ),
                prompt=prompt,
                original_error=TypeError("prompt=list is not supported; use messages="),
            )

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
            original_error=TypeError(f"Expected str or dict, got {type(prompt).__name__}"),
        )

    def _extract_prompt_and_vars(self, kwargs: Dict[str, Any]) -> tuple:
        """Split kwargs into (prompt, messages, template_vars, llm_knobs)."""
        prompt = kwargs.pop("prompt", None)
        messages = kwargs.pop("messages", None)
        llm_knobs = {}
        for key in _LLM_PARAM_KEYS:
            if key in ("prompt", "messages"):
                continue
            if key in kwargs:
                val = kwargs.pop(key)
                if val is not None:
                    llm_knobs[key] = val
        # Anything left over is a template variable
        return prompt, messages, kwargs, llm_knobs

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
        """Format prompt → LLM → optional parse+semantic-retry → optional fallback.

        Two shapes:
          * ``fields=None`` (default): raw content mode — call once, on refusal
            or SDK-side hard error try the ``fallback`` list, else propagate.
          * ``fields=[...]`` set: structured mode — call LLM, parse+validate
            inline via ``operonx.providers.parsing.parse_and_extract``. On
            parse/validator failure, retry ``max_retries`` more times on the
            SAME resource with the previous response + error injected into
            the messages (Instructor-style error-guided retry). Semantic
            failures NEVER trigger fallback — a different resource is
            unlikely to fix a parser-shape bug.

        Transport errors (429 / 5xx / timeout) are the SDK's responsibility;
        anything that surfaces here has already exhausted the SDK's own
        retries, so it counts as a hard failure and routes to ``fallback``.
        """
        llm_params = self._build_llm_params(kwargs)

        if self.batch_mode:
            self._ensure_initialized()
            if not self._batch_coordinator:
                raise RuntimeError("Batch coordinator not initialized")
            completion = await self._batch_coordinator.submit(**llm_params)
            return self._extract_completion(completion, self.resource)

        if self._extract_fields is None:
            return await self._llm_call_with_fallback(llm_params)
        return await self._structured_generate(llm_params)

    async def _llm_call_with_fallback(self, llm_params: Dict[str, Any]):
        """One LLM call. Refusal or hard exception → try fallback resources."""
        selected = self._select_llm()
        resource = self._get_resource_key(selected)

        try:
            completion = await selected.generate(**llm_params)
            result = self._extract_completion(completion, resource)
        except Exception as e:
            if not self._fallback_llms:
                raise
            LOGGER.error(f"Primary {resource} failed: {e}")
            return await self._fallback_generate(llm_params)

        if self._is_refusal(result):
            reason = result.get("finish_reason")
            refusal_text = (result.get("extras") or {}).get("refusal")
            if not self._fallback_llms:
                raise LLMRefusalError(
                    message=f"{resource} refused the request",
                    reason=reason,
                    refusal_text=refusal_text,
                    resource=resource,
                )
            LOGGER.warning(
                f"Primary {resource} refused (finish_reason={reason!r}); trying fallback"
            )
            return await self._fallback_generate(llm_params)

        return result

    async def _fallback_generate(self, llm_params):
        """Try fallback LLMs in order. Skips refusers + hard-failers; raises
        when the list is exhausted."""
        last_refusal_reason: Optional[str] = None
        for idx, fallback_llm in enumerate(self._fallback_llms):
            fallback_key = self.fallback[idx]
            try:
                LOGGER.info(f"Trying fallback {fallback_key}...")
                completion = await fallback_llm.generate(**llm_params)
                result = self._extract_completion(completion, fallback_key)
                if self._is_refusal(result):
                    last_refusal_reason = result.get("finish_reason")
                    LOGGER.warning(
                        f"Fallback {fallback_key} refused "
                        f"(finish_reason={last_refusal_reason!r}); trying next"
                    )
                    continue
                LOGGER.info(f"Fallback to {fallback_key} succeeded")
                return result
            except Exception as fallback_error:
                LOGGER.error(f"Fallback {fallback_key} failed: {fallback_error}")
        raise RuntimeError(
            f"All fallback models failed or refused (last_refusal_reason={last_refusal_reason!r})"
        )

    def _is_refusal(self, result: Dict[str, Any]) -> bool:
        """Return True when the response looks like a provider-signalled refusal.

        Detected via ``finish_reason in {"content_filter", "safety"}`` or a
        non-empty ``extras.refusal`` field. Does NOT heuristically scan
        ``content`` for phrases like "I can't help with that" — those
        false-positive too often.
        """
        if result.get("finish_reason") in ("content_filter", "safety"):
            return True
        extras = result.get("extras") or {}
        if extras.get("refusal"):
            return True
        return False

    async def _structured_generate(self, llm_params: Dict[str, Any]):
        """LLM call → parse+validate → error-guided semantic retry on failure.

        Retries do NOT trigger fallback — parse/validator errors are semantic.
        Uses the SAME LLM selection each attempt. On the final attempt, if
        parsing still fails, returns the last LLM result with all extracted
        fields set to ``None`` and ``error`` set to the last failure message —
        downstream ops branch on ``error``.
        """
        messages_base = list(llm_params.get("messages", []))
        last_error: Optional[str] = None
        last_content: Optional[str] = None
        last_result: Dict[str, Any] = {}

        for attempt in range(self.max_retries + 1):
            if attempt > 0 and self.retry_hint and last_content is not None:
                messages = messages_base + [
                    {"role": "assistant", "content": last_content},
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response failed: {last_error}. "
                            f"Please try again, following the required format exactly."
                        ),
                    },
                ]
            else:
                messages = messages_base

            attempt_params = dict(llm_params, messages=messages)
            last_result = await self._llm_call_with_fallback(attempt_params)

            parsed = parse_and_extract(
                text=last_result.get("content", ""),
                parser=self.parser,
                fields=self._extract_fields,
                validators=self.validators,
            )

            if parsed.get("error") is None:
                return {**last_result, **parsed}

            last_error = parsed["error"]
            last_content = last_result.get("content")
            LOGGER.warning(
                "LLMOp semantic failure on attempt %d/%d: %s",
                attempt + 1,
                self.max_retries + 1,
                last_error,
            )

        field_nones = {f.output_key: None for f in self._extract_fields}
        return {**last_result, **field_nones, "error": last_error}

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
        """Build the final metadata yield for a stream.

        ``content`` here is the **whole** accumulated response, not the
        remaining tail — the per-token frames already carried every piece
        of it. ``final=True`` is what separates the two; before it existed
        the only difference was the incidental presence of
        ``finish_reason``, and nothing said so.

        Consumers should either join the ``final=False`` frames or read
        this one, never both.
        """
        return {
            "role": "assistant",
            "final": True,
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
        prompt, messages, template_vars, llm_knobs = self._extract_prompt_and_vars(inputs_copy)
        built = self._resolve_messages(prompt, messages, template_vars)
        return {"messages": built, **llm_knobs}

    def _resolve_messages(
        self,
        prompt: Any,
        messages: Any,
        template_vars: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Pick the one input that was given and turn it into messages.

        Exactly one of ``prompt`` / ``messages`` — neither is a missing
        argument, both is a contradiction, and either way answering with
        one of them silently would be a guess.
        """
        if prompt is not None and messages is not None:
            raise PromptError(
                message=(
                    "prompt= and messages= are mutually exclusive. prompt= is a "
                    "template and is formatted; messages= is a conversation and "
                    "is not. Pass one."
                ),
                prompt=prompt,
                original_error=ValueError("both prompt= and messages= were given"),
            )
        if messages is not None:
            if not isinstance(messages, list):
                raise PromptError(
                    message=f"messages= must be a list, got {type(messages).__name__}",
                    prompt=messages,
                    original_error=TypeError(type(messages).__name__),
                )
            if template_vars:
                # Ignoring them would be the silent-plausible-answer shape:
                # the caller expected substitution and gets none, with the
                # wrong text reaching the model.
                raise PromptError(
                    message=(
                        f"messages= is never formatted, so the template "
                        f"variable(s) {sorted(template_vars)} could only be "
                        f"ignored. Format them upstream, or use prompt=."
                    ),
                    prompt=messages,
                    missing_vars=sorted(template_vars),
                    original_error=ValueError(f"unused: {sorted(template_vars)}"),
                )
            return list(messages)
        if prompt is None:
            raise PromptError(
                message="one of prompt= or messages= is required",
                prompt=None,
                original_error=ValueError("LLMOp received neither prompt= nor messages="),
            )
        return self._build_messages(prompt, template_vars)

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
        raw_messages = inputs.get("messages")
        if prompt is not None or raw_messages is not None:
            try:
                vars = {k: v for k, v in inputs.items() if k not in RESERVED_KEYS}
                # messages= is already built; only a template needs formatting.
                # Reading `prompt` alone here meant a messages= call traced no
                # media at all.
                messages = (
                    list(raw_messages)
                    if raw_messages is not None
                    else self._build_messages(prompt, vars)
                )
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
            # ``final=False`` marks this as a delta. The last frame of a
            # stream repeats the whole accumulated text under the same
            # ``content`` key, so a consumer that joins frames without
            # checking would emit the answer twice.
            yield_dict = {"content": choice.delta.content, "role": "assistant", "final": False}
        else:
            yield_dict = None

        if choice.finish_reason:
            acc["finish_reason"] = choice.finish_reason

        if choice.delta.tool_calls:
            LLMOp._merge_tool_call_deltas(choice.delta.tool_calls, acc)

        if hasattr(choice.delta, "refusal") and choice.delta.refusal:
            acc["refusal"] = (acc["refusal"] or "") + choice.delta.refusal

        return yield_dict

    @staticmethod
    def _merge_tool_call_deltas(deltas, acc: dict) -> None:
        """Assemble streamed tool-call deltas into whole tool calls.

        A provider streams one tool call across many chunks, each carrying
        the ``index`` it belongs to and a fragment of ``arguments``::

            {index: 0, id: "call_abc", function: {name: "get_weather"}}
            {index: 0, function: {arguments: '{"city": '}}
            {index: 0, function: {arguments: '"Hanoi"}'}}

        Appending these verbatim yields one broken call per chunk — each
        with a fragment of JSON that parses as nothing. Merging on
        ``index`` is what turns them back into a call. ``id`` and ``name``
        arrive once, usually on the first fragment, so the first non-empty
        value wins rather than the last.

        Non-streaming responses were always correct, which is why this
        survived: streaming and tool calling each worked alone, and only
        the combination — the one an agent needs — was broken.
        """
        by_index = acc.setdefault("_tool_call_index", {})
        for delta in deltas:
            data = delta.model_dump()
            index = data.get("index")
            if index is None:
                # No index to merge on: keep it whole rather than guess.
                acc["tool_calls"].append(data)
                continue

            if index not in by_index:
                entry = {
                    "id": data.get("id"),
                    "type": data.get("type") or "function",
                    "function": {"name": None, "arguments": ""},
                }
                by_index[index] = entry
                acc["tool_calls"].append(entry)

            entry = by_index[index]
            if data.get("id") and not entry.get("id"):
                entry["id"] = data["id"]
            if data.get("type"):
                entry["type"] = data["type"]

            fn = data.get("function") or {}
            if fn.get("name") and not entry["function"]["name"]:
                entry["function"]["name"] = fn["name"]
            if fn.get("arguments"):
                entry["function"]["arguments"] += fn["arguments"]

    @staticmethod
    def _new_stream_acc() -> dict:
        """Create a fresh accumulator for streaming state."""
        return {
            "response": "",
            "thinking_content": "",
            "finish_reason": "stop",
            "usage_raw": {},
            "tool_calls": [],
            # index → the entry in `tool_calls` being assembled for it.
            # Declared here so the accumulator's shape is visible; only
            # `tool_calls` is read into the op's outputs (see line ~640),
            # so this scratch key never reaches a caller.
            "_tool_call_index": {},
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
        messages=None,
        # Structured-output layer (merged from ask()/ParserOp in 1.0.0).
        fields=None,
        parser=None,
        validators=None,
        max_retries=0,
        retry_hint=True,
        **kwargs,
    ) -> "LLMOp":
        """Create an LLMOp with flat kwargs.

        Simple mode::

            llm = LLMOp.of(resource="gpt-4", prompt="Hello {name}", name="Alice")

        Conversation — a message list that is data, never formatted::

            chat = LLMOp.of(resource="gpt-4", messages=history["messages"])

        Structured mode (replaces the removed ask() helper)::

            llm = LLMOp.of(
                resource="claude-haiku",
                prompt="Classify: {speech}",
                fields=["result: str"],
                parser="xml",
                validators={"result": ["CONFIRM", "DENY", "@FALLBACK"]},
                max_retries=2,
                speech=PARENT["speech"],
            )
        """
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        if prompt is not None:
            input_mappings["prompt"] = prompt
        if messages is not None:
            input_mappings["messages"] = messages
        return cls(
            resource=resource,
            ratios=ratios,
            fallback=fallback,
            batch_mode=batch_mode,
            seed=seed,
            fields=fields,
            parser=parser,
            validators=validators,
            max_retries=max_retries,
            retry_hint=retry_hint,
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


def _check_prompt_inputs(inputs: Dict[str, Any]) -> None:
    """Reject prompt/messages mistakes that are knowable at construction.

    A ``Ref`` resolves at run time and can only be checked there — banning
    it here would outlaw the legitimate ``prompt=PARENT["template"]``
    wiring. A **literal** list, or both inputs at once, is knowable now,
    and one model call later is a much worse place to find out.
    """
    prompt = inputs.get("prompt")
    messages = inputs.get("messages")
    if prompt is not None and messages is not None:
        raise PromptError(
            message=(
                "prompt= and messages= are mutually exclusive. prompt= is a "
                "template and is formatted; messages= is a conversation and is "
                "not. Pass one."
            ),
            prompt=prompt,
            original_error=ValueError("both prompt= and messages= were given"),
        )
    if isinstance(prompt, list):
        raise PromptError(
            message=(
                "prompt= no longer accepts a list — pass it as messages=, which "
                "is never formatted. Formatting a conversation turns every brace "
                "in it (a JSON tool result, pasted code, the model's own "
                "tool-call arguments) into a template variable that does not "
                "exist. For a multi-message *template*, build the list in an "
                "upstream @op; prompt={'system': …, 'user': …} covers the "
                "two-message case."
            ),
            prompt=prompt,
            original_error=TypeError("prompt=list is not supported; use messages="),
        )


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
        except ValueError as e:
            # An unmatched brace — "cost is {" — is a format-string error,
            # not a missing variable, so it skipped the KeyError branch and
            # surfaced as a bare ValueError about format strings with no
            # mention of the prompt. Someone typing "{" in a chat message
            # got a stack trace from the formatter.
            raise PromptError(
                message=(
                    f"Malformed template: {e}. Double a literal brace ({{{{ }}}}) "
                    f"if you meant one, or pass the text as messages= if it is "
                    f"data rather than a template."
                ),
                prompt=template if template is not None else value,
                original_error=e,
            ) from e
    elif isinstance(value, dict):
        return {k: _format_value(v, vars, template) for k, v in value.items()}
    elif isinstance(value, list):
        return [_format_value(item, vars, template) for item in value]
    return value
