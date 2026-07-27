"""Tests for ask() — structured-output workflow (LLM → Parser)."""

from unittest.mock import AsyncMock, Mock, patch

import pytest


def _mock_resource_hub():
    """Create a mock ResourceHub for graph-construction tests."""
    mock_hub = Mock()
    mock_hub.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
    return mock_hub


class TestAskConstruction:
    """Tests for building ask() graphs."""

    def test_import(self):
        from operonx.providers.ops import ask

        assert ask is not None

    def test_requires_fields(self):
        from operonx.providers.ops import ask

        with pytest.raises(TypeError):
            ask(resource="gpt-4", prompt="Test")

    def test_creation(self):
        from operonx.providers.ops import ask

        with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ask(
                resource="gpt-4",
                prompt="Classify: {text}\n<category>...</category>",
                fields=["category: str", "confidence: float"],
                parser="xml",
                text="sample",
            )

            assert "parser" in node._ops
            assert "llm" in node._ops
            assert "prompt" not in node._ops  # merged into llm

    def test_with_retry(self):
        from operonx.providers.ops import ask

        with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ask(
                until="error == None",
                error="init",
                resource="gpt-4",
                prompt="Classify: {text}",
                fields=["result: str"],
                max_iterations=3,
                validators={"result": ["CONFIRM", "DENY", "FALLBACK"]},
                default={"result": "FALLBACK"},
                text="sample",
            )

            assert node._loop_config is not None
            assert node._loop_config.max_iterations == 3

    def test_with_validators(self):
        from operonx.providers.ops import ask

        with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ask(
                resource="gpt-4",
                prompt="Classify: {text}",
                fields=["intent: str"],
                validators={"intent": ["CONFIRM", "DENY"]},
                text="sample",
            )

            parser_op = node._ops["parser"]
            assert "validators" in parser_op.inputs

    def test_no_retry_is_simple_graph(self):
        from operonx.providers.ops import ask

        with patch("operonx.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ask(
                resource="gpt-4",
                prompt="Classify: {text}",
                fields=["result: str"],
                text="sample",
            )

            assert node._loop_config is None
            assert "parser" in node._ops
            assert "llm" in node._ops


class TestAskIntegration:
    """Integration tests for ask() with real ResourceHub."""

    @pytest.mark.asyncio
    async def test_structured_output(self, hub):
        from operonx.core.states import MemoryState, StateSchema
        from operonx.providers.ops import ask

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = ask(
            resource="gpt-4o",
            prompt="""Classify the sentiment of this text: "{text}"

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


class TestAskRefPrompt:
    """Tests for ask() when prompt is a Ref (inside @graph)."""

    def test_llm_schema_includes_vars_when_prompt_is_ref(self):
        from operonx.core import END, START
        from operonx.core.ops.graph.graph_op import graph
        from operonx.providers.ops import ask

        with patch("operonx.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            @graph
            def detect(prompt: str, transcript: str):
                c = ask(
                    resource="gpt-4",
                    prompt=prompt,
                    fields=["result: str"],
                    parser="json",
                    transcript=transcript,
                )
                START >> c >> END

            node = detect(
                prompt="Analyze this: {transcript}",
                transcript="Hello world",
            )

            ask_op = [op for op in node._ops.values() if hasattr(op, "_loop_config")][0]
            llm_op = ask_op._ops["llm"]
            assert "transcript" in llm_op.inputs

    def test_static_prompt_still_works(self):
        from operonx.core import END, START
        from operonx.core.ops.graph.graph_op import graph
        from operonx.providers.ops import ask

        with patch("operonx.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            @graph
            def detect(transcript: str):
                c = ask(
                    resource="gpt-4",
                    prompt="Analyze: {transcript}",
                    fields=["result: str"],
                    parser="json",
                    transcript=transcript,
                )
                START >> c >> END

            node = detect(transcript="Hello world")

            ask_op = [op for op in node._ops.values() if hasattr(op, "_loop_config")][0]
            llm_op = ask_op._ops["llm"]
            assert "transcript" in llm_op.inputs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
