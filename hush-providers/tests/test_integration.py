"""Integration tests for hush-providers nodes working together."""

import pytest
from hush.core.nodes import END, PARENT, START, GraphNode
from hush.core.states import MemoryState, StateSchema


class TestNodeIntegration:
    """Integration tests for nodes working together."""

    def test_all_nodes_importable(self):
        """Test all nodes can be imported from hush.providers."""
        from hush.providers import (
            EmbeddingNode,
            LLMChainNode,
            LLMNode,
            PromptNode,
            RerankNode,
        )

        assert LLMNode is not None
        assert EmbeddingNode is not None
        assert RerankNode is not None
        assert PromptNode is not None
        assert LLMChainNode is not None

    def test_prompt_node_with_parent_outputs(self):
        """Test PromptNode with PARENT reference for outputs."""
        from hush.providers.nodes import PromptNode

        with GraphNode(name="test_graph") as graph:
            prompt = PromptNode(
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
        """Test PromptNode standalone execution."""
        from hush.providers.nodes import PromptNode

        prompt = PromptNode(
            name="prompt",
            inputs={
                "template": {"system": "You are helpful.", "user": "Task: {task}"},
                "task": "write code",
            },
        )

        schema = StateSchema(node=prompt)
        state = MemoryState(schema)

        result = await prompt.run(state)

        assert "messages" in result
        assert len(result["messages"]) == 2
        assert result["messages"][1]["content"] == "Task: write code"


class TestPluginAutoRegistration:
    """Tests for plugin auto-registration."""

    def test_plugins_are_registered(self):
        """Test that plugins auto-register on import."""
        from hush.providers.registry import EmbeddingPlugin, LLMPlugin, RerankPlugin

        assert LLMPlugin.is_registered()
        assert EmbeddingPlugin.is_registered()
        assert RerankPlugin.is_registered()

    def test_config_classes_registered(self):
        """Test that config classes are registered in REGISTRY."""
        from hush.core.registry import REGISTRY

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
        if hub.has("llm:or-claude-4-sonnet"):
            config = hub.get_config("llm:or-claude-4-sonnet")
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
        """Test a pipeline from PromptNode to LLMNode."""
        from hush.providers.nodes import LLMNode, PromptNode

        if not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("llm:or-claude-4-sonnet not configured")

        # Create a graph with Prompt -> LLM
        with GraphNode(name="chat_pipeline") as pipeline:
            prompt = PromptNode(
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

            llm = LLMNode(
                name="llm",
                resource_key="or-claude-4-sonnet",
                inputs={"messages": prompt["messages"]},
                outputs={"*": PARENT},
            )

            START >> prompt >> llm >> END

        pipeline.build()

        schema = StateSchema(node=pipeline)
        state = MemoryState(schema, inputs={"question": "What is 2+2?"})

        result = await pipeline.run(state)

        assert "content" in result
        print(f"Pipeline result: {result['content']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
