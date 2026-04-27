"""Integration tests for operonx-providers nodes working together."""

import pytest

from operonx.core.ops import END, PARENT, START, GraphOp
from operonx.core.states import MemoryState, StateSchema


class TestNodeIntegration:
    """Integration tests for nodes working together."""

    def test_all_nodes_importable(self):
        """Test all nodes can be imported from operonx.providers."""
        from operonx.providers import (
            EmbeddingOp,
            LLMOp,
            PromptOp,
            RerankOp,
            ask,
            chat,
        )

        assert LLMOp is not None
        assert EmbeddingOp is not None
        assert RerankOp is not None
        assert PromptOp is not None
        assert chat is not None
        assert ask is not None

    def test_prompt_node_with_parent_outputs(self):
        """Test PromptOp with PARENT reference for outputs."""
        from operonx.providers.ops import PromptOp

        with GraphOp(name="test_graph") as graph:
            prompt = PromptOp(
                name="prompt",
                inputs={
                    "template": "Hello world",
                },
                outputs={"*": PARENT},
            )
            START >> prompt >> END

        graph.build()

        # PARENT outputs should be resolved - messages is forwarded
        assert "messages" in graph.outputs

    @pytest.mark.asyncio
    async def test_prompt_node_execution(self):
        """Test PromptOp standalone execution."""
        from operonx.providers.ops import PromptOp

        prompt = PromptOp(
            name="prompt",
            inputs={
                "template": {"system": "You are helpful.", "user": "Task: {task}"},
                "task": "write code",
            },
        )

        schema = StateSchema(op=prompt)
        state = MemoryState(schema)

        result = {}

        async for _, result in prompt.run(state):
            pass
        assert "messages" in result
        assert len(result["messages"]) == 2
        assert result["messages"][1]["content"] == "Task: write code"


class TestPluginAutoRegistration:
    """Tests for plugin auto-registration."""

    def test_plugins_are_registered(self):
        """Test that plugins auto-register on import."""
        from operonx.providers.registry import embedding_plugin, llm_plugin, rerank_plugin

        assert llm_plugin.is_registered()
        assert embedding_plugin.is_registered()
        assert rerank_plugin.is_registered()

    def test_config_classes_registered(self):
        """Test that config classes are registered in REGISTRY."""
        from operonx.core.registry import REGISTRY

        # Import plugins to trigger registration

        # Check configs are registered by category
        assert REGISTRY.get_class("llm") is not None
        assert REGISTRY.get_class("embedding") is not None
        assert REGISTRY.get_class("reranking") is not None


class TestResourceHubIntegration:
    """Tests for ResourceHub integration."""

    def test_hub_loads_from_yaml(self, hub):
        """Test ResourceHub loads configs from YAML."""
        # Hub should have some keys
        keys = hub.keys()
        print(f"Available resources: {keys}")

    def test_hub_has_llm_config(self, hub):
        """Test hub has LLM configurations."""
        if hub.has("llm:gpt-4o-mini"):
            config = hub.get_config("llm:gpt-4o-mini")
            assert config is not None
            print(f"LLM config: {config}")

    def test_hub_has_embedding_config(self, hub):
        """Test hub has embedding configurations."""
        if hub.has("embedding:bge-m3"):
            config = hub.get_config("embedding:bge-m3")
            assert config is not None
            print(f"Embedding config: {config}")

    def test_hub_has_reranking_config(self, hub):
        """Test hub has reranking configurations."""
        if hub.has("reranking:bge-m3-onnx"):
            config = hub.get_config("reranking:bge-m3-onnx")
            assert config is not None
            print(f"Reranking config: {config}")


class TestEndToEndPipeline:
    """End-to-end pipeline tests."""

    @pytest.mark.asyncio
    async def test_prompt_to_llm_pipeline(self, hub):
        """Test a pipeline from PromptOp to LLMOp."""
        from operonx.providers.ops import LLMOp, PromptOp

        if not hub.has("llm:gpt-4o-mini"):
            pytest.skip("llm:gpt-4o-mini not configured")

        # Create a graph with Prompt -> LLM
        with GraphOp(name="chat_pipeline") as pipeline:
            prompt = PromptOp(
                name="prompt",
                inputs={
                    "template": {
                        "system": "You are a helpful assistant.",
                        "user": "Answer briefly: {question}",
                    },
                    "*": PARENT,
                },
                outputs={"messages": PARENT["messages"]},
            )

            llm = LLMOp(
                name="llm",
                resource="gpt-4o-mini",
                inputs={"messages": prompt["messages"]},
                outputs={"*": PARENT},
            )

            START >> prompt >> llm >> END

        pipeline.build()

        schema = StateSchema(op=pipeline)
        state = MemoryState(schema, inputs={"question": "What is 2+2?"})

        result = {}

        async for _, result in pipeline.run(state):
            pass
        assert "content" in result
        print(f"Pipeline result: {result['content']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
