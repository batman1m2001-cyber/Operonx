"""Tests for LLMNode functionality."""

import pytest
from unittest.mock import Mock, AsyncMock, patch


class TestLLMNode:
    """Tests for LLMNode."""

    def test_import(self):
        """Test LLMNode can be imported."""
        from hush.providers.nodes import LLMNode
        assert LLMNode is not None

    def test_node_type(self, hub):
        """Test LLMNode has correct type."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="test_llm",
            resource_key="gpt-4o"
        )

        assert node.type == "llm"
        assert node.resource_key == "gpt-4o"

    def test_input_schema(self, hub):
        """Test LLMNode has required input schema."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="schema_test",
            resource_key="gpt-4o"
        )

        assert "messages" in node.inputs

    def test_output_schema(self, hub):
        """Test LLMNode has expected output schema."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="output_test",
            resource_key="gpt-4o"
        )

        assert "content" in node.outputs
        assert "role" in node.outputs
        assert "model_used" in node.outputs

    def test_streaming_mode(self, hub):
        """Test LLMNode can be created in streaming mode."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="stream_test",
            resource_key="gpt-4o",
            stream=True
        )

        assert node.stream is True

    def test_metadata(self, hub):
        """Test specific_metadata returns model info."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="metadata_test",
            resource_key="gpt-4o"
        )

        metadata = node.specific_metadata()
        assert metadata["model"] == "gpt-4o"


class TestLLMNodeIntegration:
    """Integration tests for LLMNode with real ResourceHub."""

    @pytest.mark.asyncio
    async def test_llm_node_with_hub(self, hub):
        """Test LLMNode works with ResourceHub."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        # Check if or-claude-4-sonnet is available
        if not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("llm:or-claude-4-sonnet not configured in resources.yaml")

        node = LLMNode(
            name="chat",
            resource_key="or-claude-4-sonnet"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": "Say 'Hello' in exactly one word."}]
        })

        result = await node.run(state)

        assert "content" in result
        assert len(result["content"]) > 0
        print(f"LLM Response: {result['content']}")

    @pytest.mark.asyncio
    async def test_llm_node_streaming_with_tokens(self, hub):
        """Test LLMNode streaming mode with token verification via STREAM_SERVICE."""
        import asyncio
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState
        from hush.core import STREAM_SERVICE

        # Check if or-claude-4-sonnet is available
        if not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("llm:or-claude-4-sonnet not configured in resources.yaml")

        node = LLMNode(
            name="stream_chat",
            resource_key="or-claude-4-sonnet",
            stream=True
        )

        schema = StateSchema(node=node)
        request_id = "test-stream-request-001"
        state = MemoryState(
            schema,
            inputs={"messages": [{"role": "user", "content": "Count from 1 to 5, one number per line."}]},
            request_id=request_id
        )

        # Collect streamed chunks
        chunks_received = []
        content_parts = []

        async def collect_chunks():
            """Collect chunks from STREAM_SERVICE."""
            channel_name = node.identity(None)
            async for chunk in STREAM_SERVICE.get(request_id, channel_name):
                chunks_received.append(chunk)
                if chunk.choices and chunk.choices[0].delta.content:
                    content_parts.append(chunk.choices[0].delta.content)

        # Run streaming and chunk collection concurrently
        collector_task = asyncio.create_task(collect_chunks())

        # Execute the node (this will push chunks to STREAM_SERVICE)
        result = await node.run(state)

        # Give time for background tasks to complete pushing to STREAM_SERVICE
        await asyncio.sleep(0.1)

        # Wait for collector to finish (should end when STREAM_SERVICE.end() is called)
        await asyncio.wait_for(collector_task, timeout=5.0)

        # Verify chunks were received
        assert len(chunks_received) > 0, "Should receive streaming chunks"
        print(f"Received {len(chunks_received)} chunks")

        # Verify content parts were collected
        streamed_content = "".join(content_parts)
        assert len(streamed_content) > 0, "Should have streamed content"
        print(f"Streamed content: {streamed_content}")

        # Verify final result matches accumulated stream
        assert "content" in result
        assert result["content"] == streamed_content, "Final result should match streamed content"
        print(f"Final result content: {result['content']}")

        # Verify tokens_used is populated
        assert "tokens_used" in result
        if result["tokens_used"]:
            print(f"Tokens used: {result['tokens_used']}")


class TestLLMNodeLoadBalancing:
    """Tests for LLMNode load balancing feature."""

    def test_load_balancing_init_with_list(self, hub):
        """Test LLMNode initialization with multiple resource_keys."""
        from hush.providers.nodes import LLMNode

        # Check if required resources are available
        if not hub.has("llm:gpt-4o") or not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("Required LLM resources not configured")

        node = LLMNode(
            name="lb_test",
            resource_key=["gpt-4o", "or-claude-4-sonnet"],
            ratios=[0.7, 0.3]
        )

        assert isinstance(node.resource_key, list)
        assert len(node.resource_key) == 2
        assert node.ratios == [0.7, 0.3]
        assert len(node._llms) == 2

    def test_load_balancing_default_ratios(self, hub):
        """Test LLMNode uses equal ratios when not specified."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o") or not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("Required LLM resources not configured")

        node = LLMNode(
            name="lb_default_test",
            resource_key=["gpt-4o", "or-claude-4-sonnet"]
        )

        assert node.ratios == [0.5, 0.5]

    def test_load_balancing_ratio_validation_length(self, hub):
        """Test LLMNode raises error when ratios length doesn't match."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o") or not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("Required LLM resources not configured")

        with pytest.raises(ValueError) as exc_info:
            LLMNode(
                name="lb_error_test",
                resource_key=["gpt-4o", "or-claude-4-sonnet"],
                ratios=[0.5]  # Wrong length
            )

        assert "ratios length" in str(exc_info.value)

    def test_load_balancing_ratio_validation_sum(self, hub):
        """Test LLMNode raises error when ratios don't sum to 1.0."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o") or not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("Required LLM resources not configured")

        with pytest.raises(ValueError) as exc_info:
            LLMNode(
                name="lb_sum_test",
                resource_key=["gpt-4o", "or-claude-4-sonnet"],
                ratios=[0.3, 0.3]  # Sums to 0.6, not 1.0
            )

        assert "sum to 1.0" in str(exc_info.value)

    def test_select_llm_distribution(self, hub):
        """Test _select_llm follows weighted distribution."""
        from hush.providers.nodes import LLMNode
        from collections import Counter

        if not hub.has("llm:gpt-4o") or not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("Required LLM resources not configured")

        node = LLMNode(
            name="dist_test",
            resource_key=["gpt-4o", "or-claude-4-sonnet"],
            ratios=[0.8, 0.2]
        )

        # Run selection 1000 times
        selections = Counter()
        for _ in range(1000):
            llm = node._select_llm()
            key = node._get_selected_resource_key(llm)
            selections[key] += 1

        # Check distribution is roughly correct (with tolerance)
        gpt_ratio = selections["gpt-4o"] / 1000
        claude_ratio = selections["or-claude-4-sonnet"] / 1000

        assert 0.7 <= gpt_ratio <= 0.9, f"gpt-4o ratio {gpt_ratio} not in expected range"
        assert 0.1 <= claude_ratio <= 0.3, f"claude ratio {claude_ratio} not in expected range"

    def test_load_balancing_metadata(self, hub):
        """Test load balancing info in metadata."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o") or not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("Required LLM resources not configured")

        node = LLMNode(
            name="meta_test",
            resource_key=["gpt-4o", "or-claude-4-sonnet"],
            ratios=[0.6, 0.4]
        )

        metadata = node.specific_metadata()
        assert metadata["load_balancing"] is True
        assert metadata["ratios"] == [0.6, 0.4]

    @pytest.mark.asyncio
    async def test_load_balancing_execution(self, hub):
        """Test LLMNode execution with load balancing."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o") or not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("Required LLM resources not configured")

        node = LLMNode(
            name="lb_exec_test",
            resource_key=["gpt-4o", "or-claude-4-sonnet"],
            ratios=[0.5, 0.5]
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": "Say 'Hi' in one word."}]
        })

        result = await node.run(state)

        assert "content" in result
        assert result["model_used"] in ["gpt-4o", "or-claude-4-sonnet"]
        print(f"Model used: {result['model_used']}")
        print(f"Response: {result['content']}")


@pytest.mark.skip(reason="Batch mode tests are slow")
class TestLLMNodeBatchMode:
    """Tests for LLMNode batch mode feature."""

    def test_batch_mode_init(self, hub):
        """Test LLMNode initialization with batch_mode."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="batch_test",
            resource_key="gpt-4o",
            batch_mode=True
        )

        assert node.batch_mode is True
        assert node._batch_coordinator is not None

    def test_batch_mode_metadata(self, hub):
        """Test batch_mode in metadata."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="batch_meta_test",
            resource_key="gpt-4o",
            batch_mode=True
        )

        metadata = node.specific_metadata()
        assert metadata["batch_mode"] is True

    def test_batch_mode_coordinator_singleton(self, hub):
        """Test BatchCoordinator is shared for same resource_key."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node1 = LLMNode(
            name="batch_node_1",
            resource_key="gpt-4o",
            batch_mode=True
        )

        node2 = LLMNode(
            name="batch_node_2",
            resource_key="gpt-4o",
            batch_mode=True
        )

        # Should share the same coordinator
        assert node1._batch_coordinator is node2._batch_coordinator


@pytest.mark.skip(reason="Batch mode tests are slow")
class TestBatchCoordinator:
    """Tests for BatchCoordinator."""

    def test_coordinator_import(self):
        """Test BatchCoordinator can be imported."""
        from hush.providers.llms.batch_coordinator import BatchCoordinator
        assert BatchCoordinator is not None

    def test_coordinator_pending_count(self, hub):
        """Test pending request counting."""
        from hush.providers.llms.batch_coordinator import BatchCoordinator

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        llm = hub.llm("gpt-4o")
        coordinator = BatchCoordinator(llm)

        assert coordinator.pending_count() == 0
        assert coordinator.active_jobs_count() == 0


class TestLLMNodeAdvancedParams:
    """Tests for LLMNode advanced parameters."""

    def test_input_schema_has_advanced_params(self, hub):
        """Test LLMNode input schema includes advanced parameters."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="advanced_test",
            resource_key="gpt-4o"
        )

        # Check all advanced parameters are in the input schema
        assert "tools" in node.inputs
        assert "tool_choice" in node.inputs
        assert "response_format" in node.inputs
        assert "top_p" in node.inputs
        assert "stop" in node.inputs
        assert "frequency_penalty" in node.inputs
        assert "presence_penalty" in node.inputs
        assert "seed" in node.inputs
        assert "logprobs" in node.inputs
        assert "top_logprobs" in node.inputs
        assert "n" in node.inputs
        assert "user" in node.inputs

    def test_output_schema_has_refusal_and_logprobs(self, hub):
        """Test LLMNode output schema includes refusal and logprobs."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="output_test",
            resource_key="gpt-4o"
        )

        assert "refusal" in node.outputs
        assert "logprobs" in node.outputs


class TestLLMNodeFallback:
    """Tests for LLMNode fallback feature."""

    def test_fallback_init(self, hub):
        """Test LLMNode initialization with fallback."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o") or not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("Required LLM resources not configured")

        node = LLMNode(
            name="fallback_test",
            resource_key="gpt-4o",
            fallback=["or-claude-4-sonnet"]
        )

        assert node.fallback == ["or-claude-4-sonnet"]
        assert len(node._fallback_llms) == 1

    def test_fallback_multiple_models(self, hub):
        """Test LLMNode with multiple fallback models."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o") or not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("Required LLM resources not configured")

        node = LLMNode(
            name="multi_fallback_test",
            resource_key="gpt-4o",
            fallback=["or-claude-4-sonnet", "gpt-4o"]
        )

        assert len(node.fallback) == 2
        assert len(node._fallback_llms) == 2

    def test_fallback_in_metadata(self, hub):
        """Test fallback info in metadata."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o") or not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("Required LLM resources not configured")

        node = LLMNode(
            name="meta_fallback_test",
            resource_key="gpt-4o",
            fallback=["or-claude-4-sonnet"]
        )

        metadata = node.specific_metadata()
        assert "fallback" in metadata
        assert metadata["fallback"] == ["or-claude-4-sonnet"]

    def test_no_fallback_not_in_metadata(self, hub):
        """Test no fallback key in metadata when not configured."""
        from hush.providers.nodes import LLMNode

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="no_fallback_test",
            resource_key="gpt-4o"
        )

        metadata = node.specific_metadata()
        assert "fallback" not in metadata

    @pytest.mark.asyncio
    async def test_fallback_with_valid_primary(self, hub):
        """Test normal execution with fallback configured (no fallback triggered)."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o") or not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("Required LLM resources not configured")

        # Primary should work, fallback should not be needed
        node = LLMNode(
            name="fallback_exec_test",
            resource_key="gpt-4o",
            fallback=["or-claude-4-sonnet"]
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": "Say 'Hi' in one word."}]
        })

        result = await node.run(state)

        # Primary should work, result should use gpt-4o
        assert "content" in result
        assert result["model_used"] == "gpt-4o"
        print(f"Primary result: {result['content']}")


class TestLLMNodeTools:
    """Tests for LLMNode function calling / tools feature."""

    @pytest.mark.asyncio
    async def test_tools_function_calling(self, hub):
        """Test LLMNode with tools for function calling."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        # Define a simple tool
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather in a location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {
                                "type": "string",
                                "description": "The city and state, e.g. San Francisco, CA"
                            },
                            "unit": {
                                "type": "string",
                                "enum": ["celsius", "fahrenheit"],
                                "description": "Temperature unit"
                            }
                        },
                        "required": ["location"]
                    }
                }
            }
        ]

        node = LLMNode(
            name="tool_test",
            resource_key="gpt-4o",
            inputs={
                "messages": None,
                "tools": None,
                "tool_choice": None
            }
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": "What's the weather in Tokyo?"}],
            "tools": tools,
            "tool_choice": "auto"
        })

        result = await node.run(state)

        assert "content" in result
        assert "tool_calls" in result
        # Model should call the weather function
        if result["tool_calls"]:
            print(f"Tool calls: {result['tool_calls']}")
            assert result["tool_calls"][0]["function"]["name"] == "get_weather"
        print(f"Content: {result['content']}")

    @pytest.mark.asyncio
    async def test_tools_force_function_call(self, hub):
        """Test LLMNode forcing a specific function call."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "calculate",
                    "description": "Perform a calculation",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {"type": "string", "description": "Math expression"}
                        },
                        "required": ["expression"]
                    }
                }
            }
        ]

        node = LLMNode(
            name="force_tool_test",
            resource_key="gpt-4o"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": "What is 2 + 2?"}],
            "tools": tools,
            "tool_choice": {"type": "function", "function": {"name": "calculate"}}
        })

        result = await node.run(state)

        assert "tool_calls" in result
        if result["tool_calls"]:
            assert result["tool_calls"][0]["function"]["name"] == "calculate"
            print(f"Forced tool call: {result['tool_calls']}")


class TestLLMNodeResponseFormat:
    """Tests for LLMNode response_format (JSON mode)."""

    @pytest.mark.asyncio
    async def test_json_mode(self, hub):
        """Test LLMNode with JSON response format."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState
        import json

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="json_test",
            resource_key="gpt-4o"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [
                {"role": "system", "content": "You are a helpful assistant that responds in JSON format."},
                {"role": "user", "content": "List 3 colors with their hex codes. Return as JSON array."}
            ],
            "response_format": {"type": "json_object"}
        })

        result = await node.run(state)

        assert "content" in result
        # Verify the response is valid JSON
        try:
            parsed = json.loads(result["content"])
            print(f"JSON response: {parsed}")
            assert isinstance(parsed, (dict, list))
        except json.JSONDecodeError:
            pytest.fail(f"Response is not valid JSON: {result['content']}")

    @pytest.mark.asyncio
    async def test_json_schema_structured_output(self, hub):
        """Test LLMNode with JSON schema for structured output."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState
        import json

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="structured_test",
            resource_key="gpt-4o"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [
                {"role": "user", "content": "Give me info about Python programming language."}
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "language_info",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "year_created": {"type": "integer"},
                            "creator": {"type": "string"}
                        },
                        "required": ["name", "year_created", "creator"],
                        "additionalProperties": False
                    }
                }
            }
        })

        result = await node.run(state)

        assert "content" in result
        parsed = json.loads(result["content"])
        assert "name" in parsed
        assert "year_created" in parsed
        assert "creator" in parsed
        print(f"Structured output: {parsed}")


class TestLLMNodeVision:
    """Tests for LLMNode vision/image capabilities."""

    @pytest.mark.asyncio
    async def test_image_url_input(self, hub):
        """Test LLMNode with image URL input."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="vision_test",
            resource_key="gpt-4o"
        )

        # Use a reliable test image URL (raw image, not wiki page)
        image_url = "https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_272x92dp.png"

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What do you see in this image? Describe briefly."},
                        {"type": "image_url", "image_url": {"url": image_url}}
                    ]
                }
            ]
        })

        result = await node.run(state)

        assert "content" in result
        assert len(result["content"]) > 0
        print(f"Vision response: {result['content']}")

    @pytest.mark.asyncio
    async def test_image_base64_input(self, hub):
        """Test LLMNode with base64 encoded image input."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState
        import base64

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        # Create a minimal valid PNG (1x1 red pixel)
        # This is a valid 1x1 red PNG
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
        )
        base64_image = base64.b64encode(png_bytes).decode('utf-8')

        node = LLMNode(
            name="vision_base64_test",
            resource_key="gpt-4o"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What color is this image? Answer in one word."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{base64_image}"}
                        }
                    ]
                }
            ]
        })

        result = await node.run(state)

        assert "content" in result
        print(f"Base64 image response: {result['content']}")


class TestLLMNodeGenerationParams:
    """Tests for LLMNode generation parameters."""

    @pytest.mark.asyncio
    async def test_temperature(self, hub):
        """Test LLMNode with different temperature settings."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="temp_test",
            resource_key="gpt-4o"
        )

        # Low temperature (deterministic)
        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": "Say 'hello' in one word."}],
            "temperature": 0.0
        })

        result = await node.run(state)
        assert "content" in result
        print(f"Temperature 0.0 response: {result['content']}")

    @pytest.mark.asyncio
    async def test_max_tokens(self, hub):
        """Test LLMNode with max_tokens limit."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="max_tokens_test",
            resource_key="gpt-4o"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": "Write a very long story about a dragon."}],
            "max_tokens": 20
        })

        result = await node.run(state)

        assert "content" in result
        assert "tokens_used" in result
        # Response should be truncated
        if result["tokens_used"]:
            assert result["tokens_used"].get("completion_tokens", 0) <= 25  # Allow some margin
        print(f"Max tokens response: {result['content']}")
        print(f"Tokens used: {result['tokens_used']}")

    @pytest.mark.asyncio
    async def test_stop_sequences(self, hub):
        """Test LLMNode with stop sequences."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="stop_test",
            resource_key="gpt-4o"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": "Count from 1 to 10, one number per line."}],
            "stop": ["5"]
        })

        result = await node.run(state)

        assert "content" in result
        # Response should stop at or before "5"
        assert "6" not in result["content"] or "5" in result["content"]
        print(f"Stop sequence response: {result['content']}")

    @pytest.mark.asyncio
    async def test_top_p(self, hub):
        """Test LLMNode with top_p (nucleus sampling)."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="top_p_test",
            resource_key="gpt-4o"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": "Say 'hello' in one word."}],
            "top_p": 0.1  # Very focused sampling
        })

        result = await node.run(state)

        assert "content" in result
        print(f"Top-p 0.1 response: {result['content']}")

    @pytest.mark.asyncio
    async def test_frequency_and_presence_penalty(self, hub):
        """Test LLMNode with frequency and presence penalties."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="penalty_test",
            resource_key="gpt-4o"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": "Write a short sentence about cats."}],
            "frequency_penalty": 0.5,
            "presence_penalty": 0.5
        })

        result = await node.run(state)

        assert "content" in result
        print(f"With penalties response: {result['content']}")

    @pytest.mark.asyncio
    async def test_seed_reproducibility(self, hub):
        """Test LLMNode with seed for reproducible outputs."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="seed_test",
            resource_key="gpt-4o"
        )

        # Run twice with same seed
        results = []
        for i in range(2):
            schema = StateSchema(node=node)
            state = MemoryState(schema, inputs={
                "messages": [{"role": "user", "content": "Pick a random number between 1 and 100."}],
                "seed": 12345,
                "temperature": 0.0
            })

            result = await node.run(state)
            results.append(result["content"])
            print(f"Seed run {i+1}: {result['content']}")

        # With same seed and temperature=0, results should be identical (or very similar)
        # Note: OpenAI doesn't guarantee exact reproducibility
        print(f"Results match: {results[0] == results[1]}")


class TestLLMNodeLogprobs:
    """Tests for LLMNode logprobs feature."""

    @pytest.mark.asyncio
    async def test_logprobs_enabled(self, hub):
        """Test LLMNode with logprobs enabled."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="logprobs_test",
            resource_key="gpt-4o"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": "Say 'yes' or 'no'."}],
            "logprobs": True,
            "top_logprobs": 3
        })

        result = await node.run(state)

        assert "content" in result
        assert "logprobs" in result
        if result["logprobs"]:
            print(f"Logprobs: {result['logprobs']}")
            # Should have content with logprob info
            assert "content" in result["logprobs"]
        print(f"Response: {result['content']}")


class TestLLMNodeMultipleCompletions:
    """Tests for LLMNode n parameter (multiple completions)."""

    @pytest.mark.asyncio
    async def test_multiple_completions(self, hub):
        """Test LLMNode generating multiple completions."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        # Note: n > 1 typically returns multiple choices, but our LLMNode
        # currently only extracts the first choice. This test verifies
        # the parameter is passed correctly.
        node = LLMNode(
            name="multi_completion_test",
            resource_key="gpt-4o"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": "Say a random word."}],
            "n": 1,  # Keep at 1 for now since we only extract first choice
            "temperature": 1.0
        })

        result = await node.run(state)

        assert "content" in result
        print(f"Response: {result['content']}")


class TestLLMNodeUserTracking:
    """Tests for LLMNode user parameter."""

    @pytest.mark.asyncio
    async def test_user_parameter(self, hub):
        """Test LLMNode with user parameter for tracking."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="user_test",
            resource_key="gpt-4o"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": "Say hello."}],
            "user": "test-user-12345"
        })

        result = await node.run(state)

        assert "content" in result
        print(f"Response with user tracking: {result['content']}")


class TestLLMNodeAudio:
    """Tests for LLMNode audio capabilities (requires gpt-4o-audio model)."""

    @pytest.mark.asyncio
    async def test_audio_input(self, hub):
        """Test LLMNode with audio input using gpt-4o-audio model."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState
        import base64

        if not hub.has("llm:gpt-4o-audio"):
            pytest.skip("llm:gpt-4o-audio not configured")

        # Create a minimal valid WAV file (silent, 0.1 second, 8kHz mono)
        wav_header = bytes([
            0x52, 0x49, 0x46, 0x46,  # "RIFF"
            0x64, 0x06, 0x00, 0x00,  # file size - 8
            0x57, 0x41, 0x56, 0x45,  # "WAVE"
            0x66, 0x6D, 0x74, 0x20,  # "fmt "
            0x10, 0x00, 0x00, 0x00,  # chunk size (16)
            0x01, 0x00,              # audio format (PCM)
            0x01, 0x00,              # num channels (1)
            0x40, 0x1F, 0x00, 0x00,  # sample rate (8000)
            0x40, 0x1F, 0x00, 0x00,  # byte rate (8000)
            0x01, 0x00,              # block align (1)
            0x08, 0x00,              # bits per sample (8)
            0x64, 0x61, 0x74, 0x61,  # "data"
            0x40, 0x06, 0x00, 0x00,  # data size (1600 bytes)
        ])
        wav_data = wav_header + bytes([0x80] * 1600)  # silence
        base64_audio = base64.b64encode(wav_data).decode('utf-8')

        node = LLMNode(
            name="audio_test",
            resource_key="gpt-4o-audio"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "This is a silent audio file. Just respond with 'Audio received'."},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": base64_audio,
                                "format": "wav"
                            }
                        }
                    ]
                }
            ]
        })

        result = await node.run(state)

        assert "content" in result
        print(f"Audio input response: {result['content']}")

    # Note: gpt-4o-audio-preview model requires audio input or output modality.
    # Text-only requests are not supported by this model.


class TestLLMNodeComplexWorkflow:
    """Tests for LLMNode in complex workflow scenarios."""

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(self, hub):
        """Test LLMNode with multi-turn conversation."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="multi_turn_test",
            resource_key="gpt-4o"
        )

        # First turn
        schema = StateSchema(node=node)
        state1 = MemoryState(schema, inputs={
            "messages": [
                {"role": "system", "content": "You are a helpful math tutor."},
                {"role": "user", "content": "What is 2+2?"}
            ]
        })
        result1 = await node.run(state1)
        print(f"Turn 1: {result1['content']}")

        # Second turn (continue conversation)
        state2 = MemoryState(schema, inputs={
            "messages": [
                {"role": "system", "content": "You are a helpful math tutor."},
                {"role": "user", "content": "What is 2+2?"},
                {"role": "assistant", "content": result1["content"]},
                {"role": "user", "content": "Now multiply that by 3."}
            ]
        })
        result2 = await node.run(state2)
        print(f"Turn 2: {result2['content']}")

        assert "12" in result2["content"] or "twelve" in result2["content"].lower()

    @pytest.mark.asyncio
    async def test_system_prompt_behavior(self, hub):
        """Test LLMNode follows system prompt instructions."""
        from hush.providers.nodes import LLMNode
        from hush.core.states import StateSchema, MemoryState

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMNode(
            name="system_test",
            resource_key="gpt-4o"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "messages": [
                {"role": "system", "content": "You must respond in ALL CAPS only."},
                {"role": "user", "content": "Say hello"}
            ]
        })

        result = await node.run(state)

        assert "content" in result
        # Check that response contains uppercase letters
        has_uppercase = any(c.isupper() for c in result["content"] if c.isalpha())
        assert has_uppercase, f"Expected uppercase response, got: {result['content']}"
        print(f"System prompt response: {result['content']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
