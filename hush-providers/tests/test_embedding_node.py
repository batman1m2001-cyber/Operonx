"""Tests for EmbeddingNode functionality."""

import pytest
from unittest.mock import Mock, AsyncMock, patch


class TestEmbeddingNode:
    """Tests for EmbeddingNode."""

    def test_import(self):
        """Test EmbeddingNode can be imported."""
        from hush.providers.nodes import EmbeddingNode
        assert EmbeddingNode is not None

    def test_node_type(self):
        """Test EmbeddingNode has correct type."""
        from hush.providers.nodes import EmbeddingNode

        with patch('hush.providers.nodes.embedding.ResourceHub') as mock_hub:
            mock_instance = Mock()
            mock_instance.embedding.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = EmbeddingNode(
                name="test_embed",
                resource_key="bge-m3"
            )

            assert node.type == "embedding"
            assert node.resource_key == "bge-m3"

    def test_input_schema(self):
        """Test EmbeddingNode has texts input."""
        from hush.providers.nodes import EmbeddingNode

        with patch('hush.providers.nodes.embedding.ResourceHub') as mock_hub:
            mock_instance = Mock()
            mock_instance.embedding.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = EmbeddingNode(
                name="input_test",
                resource_key="bge-m3"
            )

            assert "texts" in node.inputs

    def test_output_schema(self):
        """Test EmbeddingNode has embeddings output."""
        from hush.providers.nodes import EmbeddingNode

        with patch('hush.providers.nodes.embedding.ResourceHub') as mock_hub:
            mock_instance = Mock()
            mock_instance.embedding.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = EmbeddingNode(
                name="output_test",
                resource_key="bge-m3"
            )

            assert "embeddings" in node.outputs

    def test_metadata(self):
        """Test specific_metadata returns model info."""
        from hush.providers.nodes import EmbeddingNode

        with patch('hush.providers.nodes.embedding.ResourceHub') as mock_hub:
            mock_instance = Mock()
            mock_instance.embedding.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = EmbeddingNode(
                name="metadata_test",
                resource_key="bge-m3"
            )

            metadata = node.specific_metadata()
            assert metadata["model"] == "bge-m3"


class TestEmbeddingNodeIntegration:
    """Integration tests for EmbeddingNode with real ResourceHub."""

    @pytest.mark.asyncio
    async def test_embedding_node_with_hub(self, hub):
        """Test EmbeddingNode works with ResourceHub."""
        from hush.providers.nodes import EmbeddingNode
        from hush.core.states import StateSchema, MemoryState

        # Check if bge-m3 is available
        if not hub.has("embedding:bge-m3"):
            pytest.skip("embedding:bge-m3 not configured in resources.yaml")

        node = EmbeddingNode(
            name="embed",
            resource_key="bge-m3"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "texts": ["Hello world", "How are you?"]
        })

        result = await node.run(state)

        assert "embeddings" in result
        embeddings = result["embeddings"]
        assert len(embeddings) == 2
        assert len(embeddings[0]) > 0  # Has dimensions
        print(f"Embedding dimensions: {len(embeddings[0])}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
