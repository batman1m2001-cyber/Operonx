"""LLM Node for hush-providers.

This module provides LLMOp that uses ResourceHub to access LLM resources.
Follows hush-core design patterns with Param-based schema.

Features:
- Load balancing: Use multiple resource_keys with weighted ratios
- Batch mode: Submit requests to OpenAI Batch API (50% cheaper, async processing)
- Token usage: Always returns token usage in both streaming and non-streaming modes
"""

import asyncio
import random
from datetime import datetime
from time import perf_counter
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from hush.core import LOGGER, STREAM_SERVICE
from hush.core.configs import OpType
from hush.core.ops import BaseOp
from hush.core.ops.base import shorthand, split_shorthand_kwargs
from hush.core.registry import ResourceHub, get_hub
from hush.core.utils.common import Param

if TYPE_CHECKING:
    from hush.core.states import MemoryState
    from hush.providers.llms.base import BaseLLM


class LLMOp(BaseOp):
    """LLM node for executing language model operations in workflows.

    Uses ResourceHub to access LLM resources by resource_key.
    Supports streaming, load balancing, fallback, and batch modes.

    Example:
        ```python
        from hush.core import GraphOp, START, END, PARENT
        from hush.providers import LLMOp

        # Simple usage
        with GraphOp(name="chat") as workflow:
            llm = LLMOp(
                name="chat",
                resource_key="gpt-4",
                inputs={"messages": PARENT["messages"]},
                outputs={"*": PARENT}
            )
            START >> llm >> END

        # Load balancing (70% gpt-4, 30% claude-3)
        llm = LLMOp(
            name="chat",
            resource_key=["gpt-4", "claude-3"],
            ratios=[0.7, 0.3],
            inputs={"messages": PARENT["messages"]}
        )

        # Batch mode (uses OpenAI Batch API, 50% cheaper)
        llm = LLMOp(
            name="batch_chat",
            resource_key="gpt-4",
            batch_mode=True,
            inputs={"messages": PARENT["messages"]}
        )

        workflow.build()
        ```
    """

    __slots__ = [
        "resource_key",
        "batch_mode",
        "ratios",
        "_llms",
        "_llm",
        "_batch_coordinator",
        "fallback",
        "_fallback_llms",
        "_rng",
    ]

    type: OpType = "llm"

    def __init__(
        self,
        resource_key: Optional[Union[str, List[str]]] = None,
        ratios: Optional[List[float]] = None,
        fallback: Optional[List[str]] = None,
        batch_mode: bool = False,
        seed: Optional[int] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs,
    ):
        """Initialize LLMOp.

        Args:
            resource_key: Resource key(s) for LLM in ResourceHub.
                - Single string: "gpt-4"
                - List for load balancing: ["gpt-4", "claude-3"]
            ratios: Weight ratios for load balancing. Must sum to 1.0.
                Only used when resource_key is a list.
            fallback: Fallback resource key(s) to use when primary model fails.
                List of resource keys from ResourceHub, tried in order.
            batch_mode: Whether to use OpenAI Batch API (50% cheaper, async processing)
            seed: Optional seed for load balancing RNG. Provides reproducible selection.
            inputs: Input variable mappings
            outputs: Output variable mappings
            **kwargs: Additional keyword arguments for BaseOp
        """
        # Initialize base without inputs/outputs first
        super().__init__(**kwargs)

        self.batch_mode = batch_mode
        self.contain_generation = True
        self.fallback = fallback
        # Dedicated RNG for load balancing - isolated from global random state
        self._rng = random.Random(seed)

        # Handle load balancing setup
        self._llms: List["BaseLLM"] = []
        self._llm: Optional["BaseLLM"] = None
        self._fallback_llms: List["BaseLLM"] = []
        self._batch_coordinator = None

        if isinstance(resource_key, list):
            self.resource_key = resource_key
            self.ratios = ratios or [1.0 / len(resource_key)] * len(resource_key)

            # Validate ratios
            if len(self.ratios) != len(self.resource_key):
                raise ValueError(
                    f"ratios length ({len(self.ratios)}) must match "
                    f"resource_key length ({len(self.resource_key)})"
                )
            if abs(sum(self.ratios) - 1.0) > 0.01:
                raise ValueError(f"ratios must sum to 1.0, got {sum(self.ratios)}")
        else:
            self.resource_key = resource_key
            self.ratios = [1.0] if resource_key else None

        # Define input/output schema
        input_schema = {
            "messages": Param(type=list, required=True),
            "temperature": Param(type=float, default=0.0),
            "max_tokens": Param(type=int, default=None),
            # Advanced generation parameters
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
            "tokens_used": Param(type=dict, default={}),
            "tool_calls": Param(type=list, default=[]),
            "thinking_content": Param(type=str, default=None),
            "context_used": Param(type=int, default=0),
            "refusal": Param(type=str, default=None),
            "logprobs": Param(type=dict, default=None),
            "error_code": Param(type=int, default=None),
            "error_message": Param(type=str, default=None),
        }

        # Normalize and merge with user-provided
        normalized_inputs = self._normalize_params(inputs)
        normalized_outputs = self._normalize_params(outputs)
        self.inputs = self._merge_params(input_schema, normalized_inputs)
        self.outputs = self._merge_params(output_schema, normalized_outputs)

        # Set up LLM backend(s)
        try:
            hub = ResourceHub.instance()
        except RuntimeError:
            hub = get_hub()

        # Initialize LLM(s) based on resource_key type
        if isinstance(self.resource_key, list):
            self._llms = [hub.llm(key) for key in self.resource_key]
            self._llm = self._llms[0]  # Default to first
        else:
            self._llm = hub.llm(self.resource_key)
            self._llms = [self._llm] if self._llm else []

        # Initialize fallback LLMs
        if self.fallback:
            self._fallback_llms = [hub.llm(key) for key in self.fallback]

        # Set up core function based on mode
        if self.batch_mode:
            # Batch mode: use BatchCoordinator
            from hush.providers.llms.batch_coordinator import BatchCoordinator

            # Get batch config from LLM config if available
            config = self._llm.config
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
                resource_key=self.resource_key
                if isinstance(self.resource_key, str)
                else self.resource_key[0],
                llm=self._llm,
                **batch_kwargs,
            )
            self.core = self._batch_coordinator.submit
        elif self.stream:
            self.core = self._llm.stream
        else:
            self.core = self._llm.generate

    def _select_llm(self) -> "BaseLLM":
        """Select an LLM using weighted random selection for load balancing.

        Uses dedicated RNG instance for isolation from global random state.

        Returns:
            BaseLLM: The selected LLM backend
        """
        if len(self._llms) == 1:
            return self._llms[0]

        # Weighted random selection using dedicated RNG
        return self._rng.choices(self._llms, weights=self.ratios, k=1)[0]

    def _get_selected_resource_key(self, llm: "BaseLLM") -> str:
        """Get the resource key for the selected LLM.

        Args:
            llm: The selected LLM backend

        Returns:
            str: The resource key
        """
        if isinstance(self.resource_key, list):
            idx = self._llms.index(llm)
            return self.resource_key[idx]
        return self.resource_key

    def _build_llm_params(self, _inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Build parameters dict for LLM backend call.

        Filters out None values and only includes supported LLM params.

        Args:
            _inputs: Raw inputs from state

        Returns:
            Dict with only non-None LLM parameters
        """
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
        params = {}
        for key in llm_param_keys:
            value = _inputs.get(key)
            if value is not None:
                params[key] = value
        return params

    def _extract_completion_data(
        self, completion: Any, _inputs: Dict[str, Any], selected_resource_key: str
    ) -> Dict[str, Any]:
        """Extract data from a completion response.

        Args:
            completion: The ChatCompletion response
            _inputs: The input parameters
            selected_resource_key: The resource key used

        Returns:
            Dict with extracted output data
        """
        message = completion.choices[0].message
        response = message.content or ""
        finish_reason = completion.choices[0].finish_reason
        thinking_content = ""
        tokens_used = {}
        tool_calls = []
        refusal = None
        logprobs_data = None

        # Extract thinking content (for reasoning models)
        if hasattr(message, "reasoning_content"):
            thinking_content = message.reasoning_content or ""

        # Extract usage info
        if completion.usage:
            tokens_used = completion.usage.model_dump()

        # Extract tool calls
        if message.tool_calls:
            tool_calls = [tc.model_dump() for tc in message.tool_calls]

        # Extract refusal (OpenAI safety refusals)
        if hasattr(message, "refusal"):
            refusal = message.refusal

        # Extract logprobs
        if hasattr(completion.choices[0], "logprobs") and completion.choices[0].logprobs:
            logprobs_data = (
                completion.choices[0].logprobs.model_dump()
                if hasattr(completion.choices[0].logprobs, "model_dump")
                else completion.choices[0].logprobs
            )

        return {
            "role": "assistant",
            "content": response,
            "finish_reason": finish_reason,
            "model_used": selected_resource_key or completion.model,
            "tokens_used": tokens_used,
            "tool_calls": tool_calls,
            "thinking_content": thinking_content if thinking_content else None,
            "context_used": self._estimate_context(_inputs),
            "refusal": refusal,
            "logprobs": logprobs_data,
        }

    def _estimate_context(self, _inputs: Dict[str, Any]) -> int:
        """Estimate context size from messages (rough token count)."""
        return len(str(_inputs.get("messages", []))) // 4

    async def _handle_streaming(
        self,
        llm: "BaseLLM",
        llm_params: Dict[str, Any],
        resource_key: str,
        request_id: str,
        channel_name: str,
        _inputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle streaming response from LLM.

        Accumulates chunks and pushes to STREAM_SERVICE.

        Returns:
            Dict with accumulated response data
        """
        response = ""
        thinking_content = ""
        finish_reason = "stop"
        tokens_used = {}
        tool_calls = []
        refusal = None

        async for chunk in llm.stream(**llm_params):
            # Extract usage info
            if chunk.usage:
                tokens_used = chunk.usage.model_dump()

            if chunk.choices:
                choice = chunk.choices[0]

                # Accumulate thinking content
                if hasattr(choice.delta, "reasoning_content") and choice.delta.reasoning_content:
                    thinking_content += choice.delta.reasoning_content

                # Accumulate main content
                if choice.delta.content:
                    response += choice.delta.content

                # Capture finish reason
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                # Capture tool calls
                if choice.delta.tool_calls:
                    tool_calls.extend([tc.model_dump() for tc in choice.delta.tool_calls])

                # Capture refusal (streaming)
                if hasattr(choice.delta, "refusal") and choice.delta.refusal:
                    refusal = (refusal or "") + choice.delta.refusal

            # Push chunk to STREAM_SERVICE
            asyncio.create_task(
                STREAM_SERVICE.push(request_id=request_id, channel_name=channel_name, data=chunk)
            )

        # Signal end of stream
        asyncio.create_task(STREAM_SERVICE.end(request_id, channel_name))

        return {
            "role": "assistant",
            "content": response,
            "finish_reason": finish_reason,
            "model_used": resource_key,
            "tokens_used": tokens_used,
            "tool_calls": tool_calls,
            "thinking_content": thinking_content if thinking_content else None,
            "context_used": self._estimate_context(_inputs),
            "refusal": refusal,
            "logprobs": None,
        }

    async def run(
        self,
        state: "MemoryState",
        context_id: Optional[str] = None,
        parent_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the LLM node with streaming support via STREAM_SERVICE.

        Handles streaming internally with STREAM_SERVICE.push() for each chunk.
        Supports load balancing and batch mode.
        """
        parent_name = self.father.full_name if self.father else None
        state.record_execution(self.full_name, parent_name, context_id)

        request_id = state.request_id
        start_time = datetime.now()
        perf_start = perf_counter()
        _inputs = {}
        _outputs = {}

        # Select LLM for this request (load balancing)
        selected_llm = self._select_llm() if self._llms else None
        selected_resource_key = (
            self._get_selected_resource_key(selected_llm) if selected_llm else self.resource_key
        )

        try:
            _inputs = self.get_inputs(state, context_id=context_id)
            llm_params = self._build_llm_params(_inputs)

            if self.batch_mode:
                # Batch mode: submit to BatchCoordinator
                LOGGER.info(f"Batch mode for {self.name}...")
                completion = await self._batch_coordinator.submit(**llm_params)
                _outputs = self._extract_completion_data(completion, _inputs, selected_resource_key)

            elif self.stream:
                # Streaming mode with STREAM_SERVICE
                LOGGER.info(f"Streaming mode for {self.name}...")
                stream_llm = selected_llm or self._llm
                _outputs = await self._handle_streaming(
                    llm=stream_llm,
                    llm_params=llm_params,
                    resource_key=selected_resource_key,
                    request_id=request_id,
                    channel_name=self.identity(context_id),
                    _inputs=_inputs,
                )

            else:
                # Non-streaming generate mode
                LOGGER.info(f"Generate mode for {self.name}...")
                # Use selected LLM's generate method for load balancing
                generate_fn = selected_llm.generate if selected_llm else self.core
                completion = await generate_fn(**llm_params)
                _outputs = self._extract_completion_data(completion, _inputs, selected_resource_key)

            self.store_result(state, _outputs, context_id)

        except Exception as e:
            import traceback

            primary_error = str(e)
            LOGGER.error(f"Error in node {self.name}: {primary_error}")

            # Try fallback LLMs if configured
            if self._fallback_llms and not self.batch_mode:
                for idx, fallback_llm in enumerate(self._fallback_llms):
                    fallback_key = self.fallback[idx]
                    LOGGER.info(f"Trying fallback model {fallback_key}...")
                    try:
                        if self.stream:
                            # Streaming fallback
                            _outputs = await self._handle_streaming(
                                llm=fallback_llm,
                                llm_params=llm_params,
                                resource_key=fallback_key,
                                request_id=request_id,
                                channel_name=self.identity(context_id),
                                _inputs=_inputs,
                            )
                        else:
                            # Non-streaming fallback
                            completion = await fallback_llm.generate(**llm_params)
                            _outputs = self._extract_completion_data(
                                completion, _inputs, fallback_key
                            )

                        selected_llm = fallback_llm
                        selected_resource_key = fallback_key
                        self.store_result(state, _outputs, context_id)
                        LOGGER.info(f"Fallback to {fallback_key} succeeded")
                        break  # Success, exit fallback loop

                    except Exception as fallback_error:
                        LOGGER.error(f"Fallback {fallback_key} failed: {str(fallback_error)}")
                        continue  # Try next fallback
                else:
                    # All fallbacks failed
                    error_msg = traceback.format_exc()
                    LOGGER.error(error_msg)
                    state[self.full_name, "error", context_id] = (
                        f"Primary and all fallbacks failed. Primary error: {primary_error}"
                    )
            else:
                # No fallback configured or not applicable
                error_msg = traceback.format_exc()
                LOGGER.error(error_msg)
                state[self.full_name, "error", context_id] = error_msg

        finally:
            end_time = datetime.now()
            perf_end = perf_counter()
            latency_ms = (perf_end - perf_start) * 1000
            self._log(request_id, context_id, _inputs, _outputs, latency_ms)

            # Store timing to state (critical for tracing)
            state[self.full_name, "start_time", context_id] = start_time
            state[self.full_name, "end_time", context_id] = end_time

            # Calculate cost from LLM config if cost_per_token is configured
            cost = None
            if selected_llm and hasattr(selected_llm, "config"):
                config = selected_llm.config
                cost_input = getattr(config, "cost_per_input_token", None)
                cost_output = getattr(config, "cost_per_output_token", None)
                if cost_input is not None or cost_output is not None:
                    tokens = _outputs.get("tokens_used", {})
                    input_tokens = tokens.get("prompt_tokens", 0)
                    output_tokens = tokens.get("completion_tokens", 0)
                    input_cost = input_tokens * (cost_input or 0)
                    output_cost = output_tokens * (cost_output or 0)
                    cost = {
                        "input": input_cost,
                        "output": output_cost,
                        "total": input_cost + output_cost,
                    }

            # Record trace metadata for observability (with model/usage/cost)
            state.record_trace_metadata(
                op_name=self.full_name,
                context_id=context_id,
                name=self.name,
                input_vars=list(self.inputs.keys()) if self.inputs else [],
                output_vars=list(self.outputs.keys()) if self.outputs else [],
                contain_generation=self.contain_generation,
                model=_outputs.get("model_used") or selected_resource_key,
                usage=_outputs.get("tokens_used"),
                cost=cost,
                metadata=self.metadata,
            )

        return _outputs

    @shorthand
    def of(
        cls, resource_key=None, *, ratios=None, fallback=None, batch_mode=False, seed=None, **kwargs
    ) -> "LLMOp":
        """Create an LLMOp with flat kwargs.

        Example::

            llm = LLMOp.of(resource_key="gpt-4", messages=PARENT["messages"], outputs={"*": PARENT})
            llm = LLMOp.of(resource_key=["gpt-4", "claude-3"], ratios=[0.7, 0.3], messages=PARENT["messages"])
        """
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        return cls(
            resource_key=resource_key,
            ratios=ratios,
            fallback=fallback,
            batch_mode=batch_mode,
            seed=seed,
            inputs=input_mappings or None,
            **init_kwargs,
        )

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return LLM-specific metadata dictionary."""
        metadata = {
            "model": self.resource_key,
            "batch_mode": self.batch_mode,
        }
        if isinstance(self.resource_key, list):
            metadata["load_balancing"] = True
            metadata["ratios"] = self.ratios
        if self.fallback:
            metadata["fallback"] = self.fallback
        return metadata
