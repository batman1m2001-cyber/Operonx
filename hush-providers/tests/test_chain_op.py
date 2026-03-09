"""Tests for chain() factory function."""

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestChain:
    """Tests for chain()."""

    def test_import(self):
        """Test chain can be imported."""
        from hush.providers.ops import chain

        assert chain is not None

    def test_simple_chain_creation(self):
        """Test creating a simple chain (text generation mode)."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4",
                template={"system": "You are helpful.", "user": "Help with: {task}"},
                name="simple_chain",
                task="coding",
            )

            assert node.name == "simple_chain"
            assert node.type == "graph"

    def test_structured_chain_creation(self):
        """Test creating chain with structured output (parser mode)."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4",
                template="Classify: {text}\n<category>...</category>",
                name="structured_chain",
                extract=["category: str", "confidence: float"],
                parser="xml",
                text="sample",
            )

            assert "parser" in node._ops

    def test_chain_with_messages_template(self):
        """Test creating chain with complex messages_template."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4o",
                template=[
                    {"role": "system", "content": "You are a vision expert."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Analyze: {query}"},
                            {"type": "image_url", "image_url": {"url": "{image_url}"}},
                        ],
                    },
                ],
                name="vision_chain",
                query="What is this?",
                image_url="https://...",
            )

            assert node.name == "vision_chain"

    def test_chain_has_internal_nodes(self):
        """Test that chain creates internal nodes."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4",
                template="Test {var}",
                name="internal_test",
                var="value",
            )

            # Should have internal nodes (prompt, llm)
            assert "prompt" in node._ops
            assert "llm" in node._ops

    def test_chain_with_parser_has_parser_op(self):
        """Test that chain with extract has parser node."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4",
                template="Extract: {text}",
                name="parser_test",
                extract=["result: str"],
                text="sample",
            )

            # Should have parser node when extract is provided
            assert "parser" in node._ops

    def test_contain_generation_default(self):
        """Test contain_generation is True by default."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4",
                template="Test",
                name="gen_test",
            )

            assert node.contain_generation is True

    def test_auto_naming(self):
        """Test auto-naming from assignment variable."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            my_chat = chain(resource="gpt-4", template="Hello")
            assert my_chat.name == "my_chat"


class TestChainLoadBalancing:
    """Tests for chain() load balancing features."""

    def test_load_balancing_creation(self):
        """Test creating chain with load balancing."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource=["gpt-4o", "gpt-4o-mini"],
                ratios=[0.7, 0.3],
                template={"system": "You are helpful.", "user": "Hello {user}"},
                name="lb_chain",
                user="Alice",
            )

            # Check LLMOp inside has the right config
            llm_op = node._ops["llm"]
            assert llm_op.resource == ["gpt-4o", "gpt-4o-mini"]
            assert llm_op.ratios == [0.7, 0.3]

    def test_load_balancing_with_ratios(self):
        """Test load balancing config is passed to internal LLMOp."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource=["gpt-4o", "claude-sonnet"],
                ratios=[0.6, 0.4],
                template="Test",
                name="lb_metadata_test",
            )

            llm_op = node._ops["llm"]
            assert llm_op.resource == ["gpt-4o", "claude-sonnet"]
            assert llm_op.ratios == [0.6, 0.4]


class TestChainFallback:
    """Tests for chain() fallback features."""

    def test_fallback_creation(self):
        """Test creating chain with fallback."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4o",
                fallback=["claude-sonnet", "gpt-3.5-turbo"],
                template="Hello {user}",
                name="fallback_chain",
                user="Alice",
            )

            llm_op = node._ops["llm"]
            assert llm_op.resource == "gpt-4o"
            assert llm_op.fallback == ["claude-sonnet", "gpt-3.5-turbo"]

    def test_fallback_passed_to_llm(self):
        """Test fallback is passed to internal LLMOp."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4o",
                fallback=["claude-sonnet"],
                template="Test",
                name="fallback_metadata_test",
            )

            llm_op = node._ops["llm"]
            assert llm_op.fallback == ["claude-sonnet"]


class TestChainResponseFormat:
    """Tests for chain() response_format (JSON mode) features."""

    def test_response_format_creation(self):
        """Test creating chain with response_format."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4o",
                response_format={"type": "json_object"},
                template={"system": "Return JSON.", "user": "Extract entities from: {text}"},
                name="json_chain",
                text="sample",
            )

            # response_format should be in LLM's inputs
            llm_op = node._ops["llm"]
            assert "response_format" in llm_op.inputs

    def test_response_format_json_schema(self):
        """Test creating chain with JSON schema response_format."""
        from hush.providers.ops import chain

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

            node = chain(
                resource="gpt-4o",
                response_format=json_schema,
                template="Classify: {text}",
                name="schema_chain",
                text="sample",
            )

            llm_op = node._ops["llm"]
            assert "response_format" in llm_op.inputs


class TestChainCombined:
    """Tests for chain() with combined features."""

    def test_combined_load_balancing_and_fallback(self):
        """Test chain with both load balancing and fallback."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource=["gpt-4o", "gpt-4o-mini"],
                ratios=[0.8, 0.2],
                fallback=["claude-sonnet"],
                template="Hello {user}",
                name="combined_chain",
                user="Alice",
            )

            llm_op = node._ops["llm"]
            assert llm_op.resource == ["gpt-4o", "gpt-4o-mini"]
            assert llm_op.ratios == [0.8, 0.2]
            assert llm_op.fallback == ["claude-sonnet"]

    def test_all_features_combined(self):
        """Test chain with all features."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource=["gpt-4o", "gpt-4o-mini"],
                ratios=[0.7, 0.3],
                fallback=["claude-sonnet"],
                response_format={"type": "json_object"},
                extract=["result: str"],
                parser="json",
                template={"system": "Return JSON.", "user": "Process: {text}"},
                name="full_chain",
                text="sample",
            )

            llm_op = node._ops["llm"]
            assert llm_op.resource == ["gpt-4o", "gpt-4o-mini"]
            assert llm_op.ratios == [0.7, 0.3]
            assert llm_op.fallback == ["claude-sonnet"]
            assert "parser" in node._ops


class TestChainUnifiedPrompt:
    """Tests for chain() with unified prompt parameter."""

    def test_string_prompt(self):
        """Test chain with string prompt (user message only)."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4",
                template="Hello {user}, help me with {task}.",
                name="string_prompt_chain",
                user="Alice",
                task="coding",
            )

            assert node.name == "string_prompt_chain"

    def test_dict_prompt_with_system_user(self):
        """Test chain with dict prompt containing system/user keys."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4",
                template={"system": "You are a {role}.", "user": "Help with: {task}"},
                name="dict_prompt_chain",
                role="helpful assistant",
                task="coding",
            )

            assert node.name == "dict_prompt_chain"
            assert "prompt" in node._ops

    def test_list_prompt_multimodal(self):
        """Test chain with list prompt (full messages array)."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4o",
                template=[
                    {"role": "system", "content": "You are a vision expert."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Analyze: {query}"},
                            {"type": "image_url", "image_url": {"url": "{image_url}"}},
                        ],
                    },
                ],
                name="list_prompt_chain",
                query="What is this?",
                image_url="https://example.com/image.png",
            )

            assert node.name == "list_prompt_chain"

    def test_unified_prompt_with_load_balancing(self):
        """Test unified prompt with load balancing features."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource=["gpt-4o", "gpt-4o-mini"],
                ratios=[0.7, 0.3],
                fallback=["claude-sonnet"],
                template={"system": "You are helpful.", "user": "{query}"},
                name="unified_lb_chain",
                query="Hello",
            )

            llm_op = node._ops["llm"]
            assert llm_op.resource == ["gpt-4o", "gpt-4o-mini"]
            assert llm_op.ratios == [0.7, 0.3]
            assert llm_op.fallback == ["claude-sonnet"]

    def test_unified_prompt_with_json_mode(self):
        """Test unified prompt with JSON response_format."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4o",
                response_format={"type": "json_object"},
                template={"user": "Classify and return JSON: {text}"},
                name="unified_json_chain",
                text="sample",
            )

            llm_op = node._ops["llm"]
            assert "response_format" in llm_op.inputs

    def test_unified_prompt_with_extract(self):
        """Test unified prompt with structured output parsing."""
        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chain(
                resource="gpt-4",
                template={"user": "Classify: {text}\n<category>...</category>"},
                name="unified_parser_chain",
                extract=["category: str", "confidence: float"],
                parser="xml",
                text="sample",
            )

            assert "parser" in node._ops


class TestChainIntegration:
    """Integration tests for chain() with real ResourceHub."""

    @pytest.mark.asyncio
    async def test_chain_simple_generation(self, hub):
        """Test chain simple text generation with real LLM."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import chain

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = chain(
            resource="gpt-4o",
            template={
                "system": "You are a helpful assistant.",
                "user": "Say hello to {user} in one sentence.",
            },
            name="simple_chain",
            user="Alice",
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        print(f"chain response: {result['content']}")

    @pytest.mark.asyncio
    async def test_chain_structured_output(self, hub):
        """Test chain with structured output parsing."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import chain

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = chain(
            resource="gpt-4o",
            template="""Classify the sentiment of this text: "{text}"

Output your response in XML format:
<sentiment>positive/negative/neutral</sentiment>
<confidence>0.0-1.0</confidence>""",
            name="structured_chain",
            extract=["sentiment: str", "confidence: float"],
            parser="xml",
            text="I love this product! It's amazing!",
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "sentiment" in result
        assert "confidence" in result
        print(f"Sentiment: {result['sentiment']}, Confidence: {result['confidence']}")

    @pytest.mark.asyncio
    async def test_chain_json_mode(self, hub):
        """Test chain with JSON response_format."""
        import json

        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import chain

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = chain(
            resource="gpt-4o",
            response_format={"type": "json_object"},
            template={
                "system": "You are a helpful assistant that always responds in JSON format.",
                "user": "List 3 programming languages with their year of creation. Return as JSON with 'languages' array.",
            },
            name="json_chain",
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
        """Test chain with JSON schema response_format."""
        import json

        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import chain

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = chain(
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
            template="Give me info about Python programming language.",
            name="schema_chain",
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
        """Test chain with load balancing."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import chain

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        # Use same model twice to test load balancing mechanism
        node = chain(
            resource=["gpt-4o", "gpt-4o"],  # Same model for testing
            ratios=[0.5, 0.5],
            template={"system": "You are helpful.", "user": "Say 'hello' in one word."},
            name="lb_chain",
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
        """Test chain with fallback configuration."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import chain

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        # Primary should work, fallback shouldn't be needed
        node = chain(
            resource="gpt-4o",
            fallback=["gpt-4o"],  # Same as fallback for testing
            template={"system": "You are helpful.", "user": "Say 'test' in one word."},
            name="fallback_chain",
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        print(f"Fallback chain response: {result['content']}")

    @pytest.mark.asyncio
    async def test_chain_combined_features(self, hub):
        """Test chain with load balancing + JSON mode combined."""
        import json

        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import chain

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = chain(
            resource=["gpt-4o", "gpt-4o"],
            ratios=[0.5, 0.5],
            response_format={"type": "json_object"},
            template={
                "system": "You respond in JSON format only.",
                "user": "Return a JSON object with 'greeting' key containing 'hello'.",
            },
            name="combined_chain",
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
        """Test chain with unified string prompt."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import chain

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = chain(
            resource="gpt-4o",
            template="Say hello to {user} in one sentence.",
            name="string_prompt_chain",
            user="Bob",
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        print(f"String prompt response: {result['content']}")

    @pytest.mark.asyncio
    async def test_unified_dict_prompt_generation(self, hub):
        """Test chain with unified dict prompt (system + user)."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import chain

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = chain(
            resource="gpt-4o",
            template={
                "system": "You are a friendly assistant who speaks like a {style}.",
                "user": "Greet {user}.",
            },
            name="dict_prompt_chain",
            style="pirate",
            user="Captain Jack",
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

        from hush.providers.ops import chain

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = chain(
            resource="gpt-4o",
            response_format={"type": "json_object"},
            template={
                "system": "You always respond in JSON format.",
                "user": "Return a JSON with 'message' key saying hello to {user}.",
            },
            name="unified_json_chain",
            user="World",
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

        from hush.providers.ops import chain

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured in resources.yaml")

        node = chain(
            resource=["gpt-4o", "gpt-4o"],
            ratios=[0.5, 0.5],
            template={"system": "You are helpful.", "user": "Say '{word}' in one word."},
            name="unified_lb_chain",
            word="test",
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)

        result = await node.run(state)

        assert "content" in result
        assert "model_used" in result
        print(f"Unified LB response: {result['content']}")


class TestChainRefTemplate:
    """Tests for chain() when template is a Ref (e.g. inside @graph).

    Regression tests for the bug where PromptOp's wildcard forwarding
    failed to discover template variables when the template itself was
    a PARENT ref instead of a static value.
    """

    def test_prompt_schema_includes_vars_when_template_is_ref(self):
        """When template is a Ref, PromptOp should still have template vars in its schema."""
        from hush.core import END, START
        from hush.core.ops.graph.graph_op import graph

        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            @graph
            def detect(template: str, transcript: str):
                c = chain(
                    resource="gpt-4",
                    template=template,
                    extract=["result: str"],
                    parser="json",
                    transcript=transcript,
                )
                START >> c >> END

            node = detect(
                template="Analyze this: {transcript}",
                transcript="Hello world",
            )

            # The internal PromptOp must know about 'transcript'
            chain_op = node._ops["c"]
            prompt_op = chain_op._ops["prompt"]
            assert "transcript" in prompt_op.inputs, (
                "PromptOp should discover 'transcript' from chain's inputs "
                "even when template is a Ref"
            )

    def test_prompt_schema_with_multiple_vars_and_ref_template(self):
        """Multiple template vars should all be discovered when template is a Ref."""
        from hush.core import END, START
        from hush.core.ops.graph.graph_op import graph

        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            @graph
            def detect(template: str, transcript: str, call_type: str, context: str):
                c = chain(
                    resource="gpt-4",
                    template=template,
                    extract=["result: str"],
                    parser="json",
                    transcript=transcript,
                    call_type=call_type,
                    context=context,
                )
                START >> c >> END

            node = detect(
                template="Type: {call_type}\nContext: {context}\nTranscript: {transcript}",
                transcript="Hello",
                call_type="OVERDUE",
                context="debt collection",
            )

            prompt_op = node._ops["c"]._ops["prompt"]
            assert "transcript" in prompt_op.inputs
            assert "call_type" in prompt_op.inputs
            assert "context" in prompt_op.inputs

    def test_static_template_still_works(self):
        """Existing behavior: static template should still discover vars from template text."""
        from hush.core import END, START
        from hush.core.ops.graph.graph_op import graph

        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            @graph
            def detect(transcript: str):
                c = chain(
                    resource="gpt-4",
                    template="Analyze: {transcript}",
                    extract=["result: str"],
                    parser="json",
                    transcript=transcript,
                )
                START >> c >> END

            node = detect(transcript="Hello world")

            prompt_op = node._ops["c"]._ops["prompt"]
            assert "transcript" in prompt_op.inputs

    def test_nested_graph_with_ref_template(self):
        """End-to-end: nested @graph passes Ref template to chain, vars must propagate."""
        from hush.core import END, START
        from hush.core.ops.graph.graph_op import graph

        from hush.providers.ops import chain

        with patch("hush.providers.ops.llm.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            @graph
            def inner(template: str, transcript: str):
                c = chain(
                    resource="gpt-4",
                    template=template,
                    extract=["result: str"],
                    parser="json",
                    transcript=transcript,
                )
                START >> c >> END

            @graph
            def outer(transcript: str):
                sub = inner(
                    template="Analyze: {transcript}",
                    transcript=transcript,
                )
                START >> sub >> END

            node = outer(transcript="Hello world")

            # Navigate: outer → inner → chain → prompt
            inner_op = node._ops["sub"]
            chain_op = inner_op._ops["c"]
            prompt_op = chain_op._ops["prompt"]
            assert "transcript" in prompt_op.inputs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
