"""Integration tests for hush-providers nodes working together."""

import pytest
from hush.core.ops import END, PARENT, START, GraphOp
from hush.core.states import MemoryState, StateSchema


class TestNodeIntegration:
    """Integration tests for nodes working together."""

    def test_all_nodes_importable(self):
        """Test all nodes can be imported from hush.providers."""
        from hush.providers import (
            ChainOp,
            EmbeddingOp,
            LLMOp,
            PromptOp,
            RerankOp,
        )

        assert LLMOp is not None
        assert EmbeddingOp is not None
        assert RerankOp is not None
        assert PromptOp is not None
        assert ChainOp is not None

    def test_prompt_node_with_parent_outputs(self):
        """Test PromptOp with PARENT reference for outputs."""
        from hush.providers.ops import PromptOp

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
        from hush.providers.ops import PromptOp

        prompt = PromptOp(
            name="prompt",
            inputs={
                "template": {"system": "You are helpful.", "user": "Task: {task}"},
                "task": "write code",
            },
        )

        schema = StateSchema(op=prompt)
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
        """Test a pipeline from PromptOp to LLMOp."""
        from hush.providers.ops import LLMOp, PromptOp

        if not hub.has("llm:or-claude-4-sonnet"):
            pytest.skip("llm:or-claude-4-sonnet not configured")

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
                resource_key="or-claude-4-sonnet",
                inputs={"messages": prompt["messages"]},
                outputs={"*": PARENT},
            )

            START >> prompt >> llm >> END

        pipeline.build()

        schema = StateSchema(op=pipeline)
        state = MemoryState(schema, inputs={"question": "What is 2+2?"})

        result = await pipeline.run(state)

        assert "content" in result
        print(f"Pipeline result: {result['content']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
