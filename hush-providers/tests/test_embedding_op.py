"""Tests for EmbeddingOp functionality."""

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestEmbeddingOp:
    """Tests for EmbeddingOp."""

    def test_import(self):
        """Test EmbeddingOp can be imported."""
        from hush.providers.ops import EmbeddingOp

        assert EmbeddingOp is not None

    def test_node_type(self):
        """Test EmbeddingOp has correct type."""
        from hush.providers.ops import EmbeddingOp

        with patch("hush.providers.ops.embedding.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.embedding.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = EmbeddingOp(name="test_embed", resource_key="bge-m3")

            assert node.type == "embedding"
            assert node.resource_key == "bge-m3"

    def test_input_schema(self):
        """Test EmbeddingOp has texts input."""
        from hush.providers.ops import EmbeddingOp

        with patch("hush.providers.ops.embedding.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.embedding.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = EmbeddingOp(name="input_test", resource_key="bge-m3")

            assert "texts" in node.inputs

    def test_output_schema(self):
        """Test EmbeddingOp has embeddings output."""
        from hush.providers.ops import EmbeddingOp

        with patch("hush.providers.ops.embedding.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.embedding.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = EmbeddingOp(name="output_test", resource_key="bge-m3")

            assert "embeddings" in node.outputs

    def test_metadata(self):
        """Test specific_metadata returns model info."""
        from hush.providers.ops import EmbeddingOp

        with patch("hush.providers.ops.embedding.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.embedding.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = EmbeddingOp(name="metadata_test", resource_key="bge-m3")

            metadata = node.specific_metadata
            assert metadata["model"] == "bge-m3"


class TestEmbeddingOpIntegration:
    """Integration tests for EmbeddingOp with real ResourceHub."""

    @pytest.mark.asyncio
    async def test_embedding_node_with_hub(self, hub):
        """Test EmbeddingOp works with ResourceHub."""
        from hush.core.states import MemoryState, StateSchema

        from hush.providers.ops import EmbeddingOp

        # Check if bge-m3 is available
        if not hub.has("embedding:bge-m3"):
            pytest.skip("embedding:bge-m3 not configured in resources.yaml")

        node = EmbeddingOp(name="embed", resource_key="bge-m3")

        schema = StateSchema(op=node)
        state = MemoryState(schema, inputs={"texts": ["Hello world", "How are you?"]})

        result = await node.run(state)

        assert "embeddings" in result
        embeddings = result["embeddings"]
        assert len(embeddings) == 2
        assert len(embeddings[0]) > 0  # Has dimensions
        print(f"Embedding dimensions: {len(embeddings[0])}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
