"""Tests for chat() and ask() factory functions."""

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestChat:
    """Tests for chat()."""

    def test_import(self):
        from hush.providers.ops import chat

        assert chat is not None

    def test_simple_creation(self):
        from hush.providers.ops import chat

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chat(
                resource="gpt-4",
                template={"system": "You are helpful.", "user": "Help with: {task}"},
                name="simple_chat",
                task="coding",
            )

            assert node.name == "simple_chat"
            assert node.type == "graph"

    def test_has_internal_nodes(self):
        from hush.providers.ops import chat

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chat(
                resource="gpt-4",
                template="Test {var}",
                name="internal_test",
                var="value",
            )

            assert "prompt" in node._ops
            assert "llm" in node._ops

    def test_auto_naming(self):
        from hush.providers.ops import chat

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            my_chat = chat(resource="gpt-4", template="Hello")
            assert my_chat.name == "my_chat"

    def test_with_messages_template(self):
        from hush.providers.ops import chat

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chat(
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
                name="vision_chat",
                query="What is this?",
                image_url="https://...",
            )

            assert node.name == "vision_chat"

    def test_contain_generation_explicit(self):
        from hush.providers.ops import chat

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chat(
                resource="gpt-4",
                template="Test",
                name="gen_test",
                contain_generation=True,
            )

            assert node.contain_generation is True


class TestChatLoadBalancing:
    """Tests for chat() load balancing."""

    def test_load_balancing_creation(self):
        from hush.providers.ops import chat

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chat(
                resource=["gpt-4o", "gpt-4o-mini"],
                ratios=[0.7, 0.3],
                template={"system": "You are helpful.", "user": "Hello {user}"},
                name="lb_chat",
                user="Alice",
            )

            llm_op = node._ops["llm"]
            assert llm_op.resource == ["gpt-4o", "gpt-4o-mini"]
            assert llm_op.ratios == [0.7, 0.3]

    def test_load_balancing_with_ratios(self):
        from hush.providers.ops import chat

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chat(
                resource=["gpt-4o", "claude-sonnet"],
                ratios=[0.6, 0.4],
                template="Test",
                name="lb_metadata_test",
            )

            llm_op = node._ops["llm"]
            assert llm_op.resource == ["gpt-4o", "claude-sonnet"]
            assert llm_op.ratios == [0.6, 0.4]


class TestChatFallback:
    """Tests for chat() fallback."""

    def test_fallback_creation(self):
        from hush.providers.ops import chat

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chat(
                resource="gpt-4o",
                fallback=["claude-sonnet", "gpt-3.5-turbo"],
                template="Hello {user}",
                name="fallback_chat",
                user="Alice",
            )

            llm_op = node._ops["llm"]
            assert llm_op.resource == "gpt-4o"
            assert llm_op.fallback == ["claude-sonnet", "gpt-3.5-turbo"]


class TestChatResponseFormat:
    """Tests for chat() response_format (JSON mode)."""

    def test_response_format_creation(self):
        from hush.providers.ops import chat

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chat(
                resource="gpt-4o",
                response_format={"type": "json_object"},
                template={"system": "Return JSON.", "user": "Extract entities from: {text}"},
                name="json_chat",
                text="sample",
            )

            llm_op = node._ops["llm"]
            assert "response_format" in llm_op.inputs


class TestExtract:
    """Tests for ask()."""

    def test_import(self):
        from hush.providers.ops import ask

        assert ask is not None

    def test_requires_fields(self):
        from hush.providers.ops import ask

        with pytest.raises(TypeError):
            ask(resource="gpt-4", template="Test")

    def test_creation(self):
        from hush.providers.ops import ask

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ask(
                resource="gpt-4",
                template="Classify: {text}\n<category>...</category>",
                fields=["category: str", "confidence: float"],
                parser="xml",
                text="sample",
            )

            assert "parser" in node._ops
            assert "llm" in node._ops
            assert "prompt" in node._ops

    def test_with_retry(self):
        from hush.providers.ops import ask

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ask(
                until="error == None",
                error="init",
                resource="gpt-4",
                template="Classify: {text}",
                fields=["result: str"],
                max_iterations=3,
                validators={"result": ["CONFIRM", "DENY", "FALLBACK"]},
                default={"result": "FALLBACK"},
                text="sample",
            )

            # Should be a loop graph
            assert node._loop_config is not None
            assert node._loop_config.max_iterations == 3  # retry=2 → 3 attempts

    def test_with_validators(self):
        from hush.providers.ops import ask

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ask(
                resource="gpt-4",
                template="Classify: {text}",
                fields=["intent: str"],
                validators={"intent": ["CONFIRM", "DENY"]},
                text="sample",
            )

            parser_op = node._ops["parser"]
            assert "validators" in parser_op.inputs

    def test_no_retry_is_simple_graph(self):
        from hush.providers.ops import ask

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = ask(
                resource="gpt-4",
                template="Classify: {text}",
                fields=["result: str"],
                text="sample",
            )

            # ask() is a simple @graph, no loop
            assert node._loop_config is None
            assert "parser" in node._ops
            assert "llm" in node._ops


class TestChatCombined:
    """Tests for chat() with combined features."""

    def test_combined_load_balancing_and_fallback(self):
        from hush.providers.ops import chat

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chat(
                resource=["gpt-4o", "gpt-4o-mini"],
                ratios=[0.8, 0.2],
                fallback=["claude-sonnet"],
                template="Hello {user}",
                name="combined_chat",
                user="Alice",
            )

            llm_op = node._ops["llm"]
            assert llm_op.resource == ["gpt-4o", "gpt-4o-mini"]
            assert llm_op.ratios == [0.8, 0.2]
            assert llm_op.fallback == ["claude-sonnet"]


class TestChatPromptFormats:
    """Tests for chat() with different prompt formats."""

    def test_string_prompt(self):
        from hush.providers.ops import chat

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chat(
                resource="gpt-4",
                template="Hello {user}, help me with {task}.",
                name="string_prompt_chat",
                user="Alice",
                task="coding",
            )

            assert node.name == "string_prompt_chat"

    def test_dict_prompt(self):
        from hush.providers.ops import chat

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chat(
                resource="gpt-4",
                template={"system": "You are a {role}.", "user": "Help with: {task}"},
                name="dict_prompt_chat",
                role="helpful assistant",
                task="coding",
            )

            assert node.name == "dict_prompt_chat"
            assert "prompt" in node._ops

    def test_list_prompt(self):
        from hush.providers.ops import chat

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = chat(
                resource="gpt-4o",
                template=[
                    {"role": "system", "content": "You are a vision expert."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Analyze: {query}"},
                        ],
                    },
                ],
                name="list_prompt_chat",
                query="What is this?",
            )

            assert node.name == "list_prompt_chat"


class TestChatIntegration:
    """Integration tests for chat() with real ResourceHub."""

    @pytest.mark.asyncio
    async def test_simple_generation(self, hub):
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import chat

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = chat(
            resource="gpt-4o",
            template={
                "system": "You are a helpful assistant.",
                "user": "Say hello to {user} in one sentence.",
            },
            name="simple_chat",
            user="Alice",
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)
        result = {}
        async for _, result in node.run(state):
            pass
        assert "content" in result
        print(f"chat response: {result['content']}")

    @pytest.mark.asyncio
    async def test_json_mode(self, hub):
        import json

        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import chat

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = chat(
            resource="gpt-4o",
            response_format={"type": "json_object"},
            template={
                "system": "You always respond in JSON format.",
                "user": "Return JSON with 'greeting' key containing 'hello'.",
            },
            name="json_chat",
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)
        result = {}
        async for _, result in node.run(state):
            pass
        assert "content" in result
        parsed = json.loads(result["content"])
        assert isinstance(parsed, dict)
        print(f"JSON response: {parsed}")


class TestExtractIntegration:
    """Integration tests for ask() with real ResourceHub."""

    @pytest.mark.asyncio
    async def test_structured_output(self, hub):
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import ask

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = ask(
            resource="gpt-4o",
            template="""Classify the sentiment of this text: "{text}"

Output your response in XML format:
<sentiment>positive/negative/neutral</sentiment>
<confidence>0.0-1.0</confidence>""",
            fields=["sentiment: str", "confidence: float"],
            parser="xml",
            text="I love this product! It's amazing!",
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)
        result = {}
        async for _, result in node.run(state):
            pass
        assert "sentiment" in result
        assert "confidence" in result
        print(f"Sentiment: {result['sentiment']}, Confidence: {result['confidence']}")


class TestExtractRefTemplate:
    """Tests for ask() when template is a Ref (inside @graph)."""

    def test_prompt_schema_includes_vars_when_template_is_ref(self):
        from hush.core import END, START
        from hush.core.ops.graph.graph_op import graph

        from hush.providers.ops import ask

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            @graph
            def detect(template: str, transcript: str):
                c = ask(
                    resource="gpt-4",
                    template=template,
                    fields=["result: str"],
                    parser="json",
                    transcript=transcript,
                )
                START >> c >> END

            node = detect(
                template="Analyze this: {transcript}",
                transcript="Hello world",
            )

            # ask() returns GraphOp — find it by type
            extract_op = [op for op in node._ops.values() if hasattr(op, "_loop_config")][0]
            prompt_op = extract_op._ops["prompt"]
            assert "transcript" in prompt_op.inputs

    def test_static_template_still_works(self):
        from hush.core import END, START
        from hush.core.ops.graph.graph_op import graph

        from hush.providers.ops import ask

        with patch("hush.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            @graph
            def detect(transcript: str):
                c = ask(
                    resource="gpt-4",
                    template="Analyze: {transcript}",
                    fields=["result: str"],
                    parser="json",
                    transcript=transcript,
                )
                START >> c >> END

            node = detect(transcript="Hello world")

            extract_op = [op for op in node._ops.values() if hasattr(op, "_loop_config")][0]
            prompt_op = extract_op._ops["prompt"]
            assert "transcript" in prompt_op.inputs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
