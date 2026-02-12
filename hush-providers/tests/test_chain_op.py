"""Tests for ChainOp functionality."""

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestChainOp:
    """Tests for ChainOp."""

    def test_import(self):
        """Test ChainOp can be imported."""
        from hush.providers.ops import ChainOp

        assert ChainOp is not None

    def test_simple_chain_creation(self):
        """Test creating a simple ChainOp (text generation mode)."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="simple_chain",
                resource="gpt-4",
                inputs={
                    "template": {"system": "You are helpful.", "user": "Help with: {task}"},
                    "task": "coding",
                },
            )

            assert node.name == "simple_chain"
            assert node.type == "graph"  # ChainOp is a GraphOp
            assert node.resource == "gpt-4"

    def test_structured_chain_creation(self):
        """Test creating ChainOp with structured output (parser mode)."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="structured_chain",
                resource="gpt-4",
                inputs={"template": "Classify: {text}\n<category>...</category>", "text": "sample"},
                extract=["category: str", "confidence: float"],
                parser="xml",
            )

            assert node.extract == ["category: str", "confidence: float"]
            assert node.parser == "xml"

    def test_chain_with_messages_template(self):
        """Test creating ChainOp with complex messages_template."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="vision_chain",
                resource="gpt-4o",
                inputs={
                    "template": [
                        {"role": "system", "content": "You are a vision expert."},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Analyze: {query}"},
                                {"type": "image_url", "image_url": {"url": "{image_url}"}},
                            ],
                        },
                    ],
                    "query": "What is this?",
                    "image_url": "https://...",
                },
            )

            assert node.name == "vision_chain"

    def test_chain_has_internal_nodes(self):
        """Test that ChainOp creates internal nodes."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="internal_test",
                resource="gpt-4",
                inputs={"template": "Test {var}", "var": "value"},
            )

            # Should have internal nodes (prompt, llm)
            assert "prompt" in node._ops
            assert "llm" in node._ops

    def test_chain_with_parser_has_parser_op(self):
        """Test that ChainOp with extract has parser node."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="parser_test",
                resource="gpt-4",
                inputs={"template": "Extract: {text}", "text": "sample"},
                extract=["result: str"],
            )

            # Should have parser node when extract is provided
            assert "parser" in node._ops

    def test_metadata(self):
        """Test specific_metadata returns chain info."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="metadata_test",
                resource="gpt-4",
                inputs={"template": {"system": "System", "user": "User {var}"}, "var": "value"},
                extract=["field: str"],
                parser="json",
            )

            metadata = node.specific_metadata
            assert metadata["resource"] == "gpt-4"
            assert metadata["extract"] == ["field: str"]
            assert metadata["parser"] == "json"


class TestChainOpLoadBalancing:
    """Tests for ChainOp load balancing features."""

    def test_load_balancing_creation(self):
        """Test creating ChainOp with load balancing."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="lb_chain",
                resource=["gpt-4o", "gpt-4o-mini"],
                ratios=[0.7, 0.3],
                inputs={
                    "template": {"system": "You are helpful.", "user": "Hello {name}"},
                    "name": "Alice",
                },
            )

            assert node.resource == ["gpt-4o", "gpt-4o-mini"]
            assert node.ratios == [0.7, 0.3]

    def test_load_balancing_metadata(self):
        """Test metadata includes load balancing info."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="lb_metadata_test",
                resource=["gpt-4o", "claude-sonnet"],
                ratios=[0.6, 0.4],
                inputs={"template": "Test"},
            )

            metadata = node.specific_metadata
            assert metadata["resource"] == ["gpt-4o", "claude-sonnet"]
            assert metadata["load_balancing"] is True
            assert metadata["ratios"] == [0.6, 0.4]


class TestChainOpFallback:
    """Tests for ChainOp fallback features."""

    def test_fallback_creation(self):
        """Test creating ChainOp with fallback."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="fallback_chain",
                resource="gpt-4o",
                fallback=["claude-sonnet", "gpt-3.5-turbo"],
                inputs={"template": "Hello {name}", "name": "Alice"},
            )

            assert node.resource == "gpt-4o"
            assert node.fallback == ["claude-sonnet", "gpt-3.5-turbo"]

    def test_fallback_metadata(self):
        """Test metadata includes fallback info."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="fallback_metadata_test",
                resource="gpt-4o",
                fallback=["claude-sonnet"],
                inputs={"template": "Test"},
            )

            metadata = node.specific_metadata
            assert metadata["fallback"] == ["claude-sonnet"]


class TestChainOpResponseFormat:
    """Tests for ChainOp response_format (JSON mode) features."""

    def test_response_format_creation(self):
        """Test creating ChainOp with response_format."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="json_chain",
                resource="gpt-4o",
                response_format={"type": "json_object"},
                inputs={
                    "template": {"system": "Return JSON.", "user": "Extract entities from: {text}"},
                    "text": "sample",
                },
            )

            assert node.response_format == {"type": "json_object"}

    def test_response_format_json_schema(self):
        """Test creating ChainOp with JSON schema response_format."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            json_schema = {
                "type": "json_schema",
                "json_schema": {
                    "name": "classification",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "category": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": ["category", "confidence"],
                    },
                },
            }

            node = ChainOp(
                name="schema_chain",
                resource="gpt-4o",
                response_format=json_schema,
                inputs={"template": "Classify: {text}", "text": "sample"},
            )

            assert node.response_format == json_schema

    def test_response_format_metadata(self):
        """Test metadata includes response_format info."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="rf_metadata_test",
                resource="gpt-4o",
                response_format={"type": "json_object"},
                inputs={"template": "Test"},
            )

            metadata = node.specific_metadata
            assert metadata["response_format"] == {"type": "json_object"}


class TestChainOpCombined:
    """Tests for ChainOp with combined features."""

    def test_combined_load_balancing_and_fallback(self):
        """Test ChainOp with both load balancing and fallback."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="combined_chain",
                resource=["gpt-4o", "gpt-4o-mini"],
                ratios=[0.8, 0.2],
                fallback=["claude-sonnet"],
                inputs={"template": "Hello {name}", "name": "Alice"},
            )

            assert node.resource == ["gpt-4o", "gpt-4o-mini"]
            assert node.ratios == [0.8, 0.2]
            assert node.fallback == ["claude-sonnet"]

            metadata = node.specific_metadata
            assert metadata["load_balancing"] is True
            assert metadata["ratios"] == [0.8, 0.2]
            assert metadata["fallback"] == ["claude-sonnet"]

    def test_all_features_combined(self):
        """Test ChainOp with all new features."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="full_chain",
                resource=["gpt-4o", "gpt-4o-mini"],
                ratios=[0.7, 0.3],
                fallback=["claude-sonnet"],
                response_format={"type": "json_object"},
                extract=["result: str"],
                parser="json",
                inputs={
                    "template": {"system": "Return JSON.", "user": "Process: {text}"},
                    "text": "sample",
                },
            )

            metadata = node.specific_metadata
            assert metadata["resource"] == ["gpt-4o", "gpt-4o-mini"]
            assert metadata["load_balancing"] is True
            assert metadata["ratios"] == [0.7, 0.3]
            assert metadata["fallback"] == ["claude-sonnet"]
            assert metadata["response_format"] == {"type": "json_object"}
            assert metadata["extract"] == ["result: str"]
            assert metadata["parser"] == "json"


class TestChainOpUnifiedPrompt:
    """Tests for ChainOp with unified prompt parameter."""

    def test_string_prompt(self):
        """Test ChainOp with string prompt (user message only)."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="string_prompt_chain",
                resource="gpt-4",
                inputs={
                    "template": "Hello {name}, help me with {task}.",
                    "name": "Alice",
                    "task": "coding",
                },
            )

            assert node.name == "string_prompt_chain"
            assert node.resource == "gpt-4"

    def test_dict_prompt_with_system_user(self):
        """Test ChainOp with dict prompt containing system/user keys."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="dict_prompt_chain",
                resource="gpt-4",
                inputs={
                    "template": {"system": "You are a {role}.", "user": "Help with: {task}"},
                    "role": "helpful assistant",
                    "task": "coding",
                },
            )

            assert node.name == "dict_prompt_chain"
            assert "prompt" in node._ops

    def test_list_prompt_multimodal(self):
        """Test ChainOp with list prompt (full messages array)."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="list_prompt_chain",
                resource="gpt-4o",
                inputs={
                    "template": [
                        {"role": "system", "content": "You are a vision expert."},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Analyze: {query}"},
                                {"type": "image_url", "image_url": {"url": "{image_url}"}},
                            ],
                        },
                    ],
                    "query": "What is this?",
                    "image_url": "https://example.com/image.png",
                },
            )

            assert node.name == "list_prompt_chain"

    def test_unified_prompt_with_load_balancing(self):
        """Test unified prompt with load balancing features."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="unified_lb_chain",
                resource=["gpt-4o", "gpt-4o-mini"],
                ratios=[0.7, 0.3],
                fallback=["claude-sonnet"],
                inputs={
                    "template": {"system": "You are helpful.", "user": "{query}"},
                    "query": "Hello",
                },
            )

            assert node.resource == ["gpt-4o", "gpt-4o-mini"]
            assert node.ratios == [0.7, 0.3]
            assert node.fallback == ["claude-sonnet"]

    def test_unified_prompt_with_json_mode(self):
        """Test unified prompt with JSON response_format."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="unified_json_chain",
                resource="gpt-4o",
                response_format={"type": "json_object"},
                inputs={"template": {"user": "Classify and return JSON: {text}"}, "text": "sample"},
            )

            assert node.response_format == {"type": "json_object"}

    def test_unified_prompt_with_extract(self):
        """Test unified prompt with structured output parsing."""
        from hush.providers.ops import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ChainOp(
                name="unified_parser_chain",
                resource="gpt-4",
                inputs={
                    "template": {"user": "Classify: {text}\n<category>...</category>"},
                    "text": "sample",
                },
                extract=["category: str", "confidence: float"],
                parser="xml",
            )

            assert node.extract == ["category: str", "confidence: float"]
            assert "parser" in node._ops


class TestChainOpIntegration:
    """Integration tests for ChainOp with real ResourceHub."""

    @pytest.mark.asyncio
    async def test_chain_simple_generation(self, hub):
        """Test ChainOp simple text generation with real LLM."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import ChainOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = ChainOp(
            name="simple_chain",
            resource="gpt-4o",
            inputs={
                "template": {
                    "system": "You are a helpful assistant.",
                    "user": "Say hello to {name} in one sentence.",
                },
                "name": "Alice",
            },
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        print(f"ChainOp response: {result['content']}")

    @pytest.mark.asyncio
    async def test_chain_structured_output(self, hub):
        """Test ChainOp with structured output parsing."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import ChainOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = ChainOp(
            name="structured_chain",
            resource="gpt-4o",
            inputs={
                "template": """Classify the sentiment of this text: "{text}"

Output your response in XML format:
<sentiment>positive/negative/neutral</sentiment>
<confidence>0.0-1.0</confidence>""",
                "text": "I love this product! It's amazing!",
            },
            extract=["sentiment: str", "confidence: float"],
            parser="xml",
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "sentiment" in result
        assert "confidence" in result
        print(f"Sentiment: {result['sentiment']}, Confidence: {result['confidence']}")

    @pytest.mark.asyncio
    async def test_chain_json_mode(self, hub):
        """Test ChainOp with JSON response_format."""
        import json

        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import ChainOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = ChainOp(
            name="json_chain",
            resource="gpt-4o",
            response_format={"type": "json_object"},
            inputs={
                "template": {
                    "system": "You are a helpful assistant that always responds in JSON format.",
                    "user": "List 3 programming languages with their year of creation. Return as JSON with 'languages' array.",
                },
            },
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        # Verify it's valid JSON
        parsed = json.loads(result["content"])
        assert isinstance(parsed, dict)
        print(f"JSON response: {parsed}")

    @pytest.mark.asyncio
    async def test_chain_json_schema(self, hub):
        """Test ChainOp with JSON schema response_format."""
        import json

        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import ChainOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = ChainOp(
            name="schema_chain",
            resource="gpt-4o",
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "language_info",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "year": {"type": "integer"},
                            "paradigm": {"type": "string"},
                        },
                        "required": ["name", "year", "paradigm"],
                        "additionalProperties": False,
                    },
                },
            },
            inputs={"template": "Give me info about Python programming language."},
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        parsed = json.loads(result["content"])
        assert "name" in parsed
        assert "year" in parsed
        assert "paradigm" in parsed
        print(f"Structured JSON: {parsed}")

    @pytest.mark.asyncio
    async def test_chain_load_balancing(self, hub):
        """Test ChainOp with load balancing."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import ChainOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        # Use same model twice to test load balancing mechanism
        node = ChainOp(
            name="lb_chain",
            resource=["gpt-4o", "gpt-4o"],  # Same model for testing
            ratios=[0.5, 0.5],
            inputs={"template": {"system": "You are helpful.", "user": "Say 'hello' in one word."}},
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        assert "model_used" in result
        print(f"Load balanced response: {result['content']}")
        print(f"Model used: {result['model_used']}")

    @pytest.mark.asyncio
    async def test_chain_with_fallback(self, hub):
        """Test ChainOp with fallback configuration."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import ChainOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        # Primary should work, fallback shouldn't be needed
        node = ChainOp(
            name="fallback_chain",
            resource="gpt-4o",
            fallback=["gpt-4o"],  # Same as fallback for testing
            inputs={"template": {"system": "You are helpful.", "user": "Say 'test' in one word."}},
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        print(f"Fallback chain response: {result['content']}")

    @pytest.mark.asyncio
    async def test_chain_combined_features(self, hub):
        """Test ChainOp with load balancing + JSON mode combined."""
        import json

        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import ChainOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = ChainOp(
            name="combined_chain",
            resource=["gpt-4o", "gpt-4o"],
            ratios=[0.5, 0.5],
            response_format={"type": "json_object"},
            inputs={
                "template": {
                    "system": "You respond in JSON format only.",
                    "user": "Return a JSON object with 'greeting' key containing 'hello'.",
                }
            },
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        parsed = json.loads(result["content"])
        assert "greeting" in parsed
        print(f"Combined features response: {parsed}")

    @pytest.mark.asyncio
    async def test_unified_string_prompt_generation(self, hub):
        """Test ChainOp with unified string prompt."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import ChainOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = ChainOp(
            name="string_prompt_chain",
            resource="gpt-4o",
            inputs={"template": "Say hello to {name} in one sentence.", "name": "Bob"},
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        print(f"String prompt response: {result['content']}")

    @pytest.mark.asyncio
    async def test_unified_dict_prompt_generation(self, hub):
        """Test ChainOp with unified dict prompt (system + user)."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import ChainOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = ChainOp(
            name="dict_prompt_chain",
            resource="gpt-4o",
            inputs={
                "template": {
                    "system": "You are a friendly assistant who speaks like a {style}.",
                    "user": "Greet {name}.",
                },
                "style": "pirate",
                "name": "Captain Jack",
            },
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        print(f"Dict prompt response: {result['content']}")

    @pytest.mark.asyncio
    async def test_unified_prompt_with_json_mode(self, hub):
        """Test unified prompt combined with JSON response mode."""
        import json

        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import ChainOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = ChainOp(
            name="unified_json_chain",
            resource="gpt-4o",
            response_format={"type": "json_object"},
            inputs={
                "template": {
                    "system": "You always respond in JSON format.",
                    "user": "Return a JSON with 'message' key saying hello to {name}.",
                },
                "name": "World",
            },
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        parsed = json.loads(result["content"])
        assert "message" in parsed
        print(f"Unified JSON response: {parsed}")

    @pytest.mark.asyncio
    async def test_unified_prompt_with_load_balancing(self, hub):
        """Test unified prompt with load balancing."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import ChainOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = ChainOp(
            name="unified_lb_chain",
            resource=["gpt-4o", "gpt-4o"],
            ratios=[0.5, 0.5],
            inputs={
                "template": {"system": "You are helpful.", "user": "Say '{word}' in one word."},
                "word": "test",
            },
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        assert "model_used" in result
        print(f"Unified LB response: {result['content']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
