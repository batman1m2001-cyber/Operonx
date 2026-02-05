"""Tests for RerankNode functionality."""

import pytest
from unittest.mock import Mock, AsyncMock, patch


class TestRerankNode:
    """Tests for RerankNode."""

    def test_import(self):
        """Test RerankNode can be imported."""
        from hush.providers.nodes import RerankNode
        assert RerankNode is not None

    def test_node_type(self):
        """Test RerankNode has correct type."""
        from hush.providers.nodes import RerankNode

        with patch('hush.providers.nodes.rerank.ResourceHub') as mock_hub:
            mock_instance = Mock()
            mock_instance.reranker.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = RerankNode(
                name="test_rerank",
                resource_key="bge-m3"
            )

            assert node.type == "rerank"
            assert node.resource_key == "bge-m3"

    def test_input_schema(self):
        """Test RerankNode has query and documents inputs."""
        from hush.providers.nodes import RerankNode

        with patch('hush.providers.nodes.rerank.ResourceHub') as mock_hub:
            mock_instance = Mock()
            mock_instance.reranker.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = RerankNode(
                name="input_test",
                resource_key="bge-m3"
            )

            assert "query" in node.inputs
            assert "documents" in node.inputs
            assert "top_k" in node.inputs
            assert "threshold" in node.inputs

    def test_output_schema(self):
        """Test RerankNode has reranks output."""
        from hush.providers.nodes import RerankNode

        with patch('hush.providers.nodes.rerank.ResourceHub') as mock_hub:
            mock_instance = Mock()
            mock_instance.reranker.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = RerankNode(
                name="output_test",
                resource_key="bge-m3"
            )

            assert "reranks" in node.outputs

    def test_metadata(self):
        """Test specific_metadata returns model info."""
        from hush.providers.nodes import RerankNode

        with patch('hush.providers.nodes.rerank.ResourceHub') as mock_hub:
            mock_instance = Mock()
            mock_instance.reranker.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = RerankNode(
                name="metadata_test",
                resource_key="bge-m3"
            )

            metadata = node.specific_metadata()
            assert metadata["model"] == "bge-m3"

    @pytest.mark.asyncio
    async def test_process_string_documents(self):
        """Test processing list of string documents."""
        from hush.providers.nodes import RerankNode

        with patch('hush.providers.nodes.rerank.ResourceHub') as mock_hub:
            mock_reranker = Mock()
            mock_reranker.run = AsyncMock(return_value=[
                {"index": 1, "score": 0.9},
                {"index": 0, "score": 0.7}
            ])
            mock_instance = Mock()
            mock_instance.reranker.return_value = mock_reranker
            mock_hub.instance.return_value = mock_instance

            node = RerankNode(
                name="process_test",
                resource_key="bge-m3"
            )

            result = await node._process(
                query="test query",
                documents=["doc1", "doc2"],
                top_k=2,
                threshold=0.0
            )

            assert "reranks" in result
            assert len(result["reranks"]) == 2
            assert result["reranks"][0]["score"] == 0.9

    @pytest.mark.asyncio
    async def test_process_dict_documents(self):
        """Test processing list of dict documents."""
        from hush.providers.nodes import RerankNode

        with patch('hush.providers.nodes.rerank.ResourceHub') as mock_hub:
            mock_reranker = Mock()
            mock_reranker.run = AsyncMock(return_value=[
                {"index": 0, "score": 0.95}
            ])
            mock_instance = Mock()
            mock_instance.reranker.return_value = mock_reranker
            mock_hub.instance.return_value = mock_instance

            node = RerankNode(
                name="dict_test",
                resource_key="bge-m3"
            )

            result = await node._process(
                query="test",
                documents=[
                    {"id": 1, "content": "doc1"},
                    {"id": 2, "content": "doc2"}
                ],
                top_k=1,
                threshold=0.0
            )

            assert "reranks" in result
            assert "id" in result["reranks"][0]
            assert "score" in result["reranks"][0]

    @pytest.mark.asyncio
    async def test_process_empty_documents(self):
        """Test processing empty document list."""
        from hush.providers.nodes import RerankNode

        with patch('hush.providers.nodes.rerank.ResourceHub') as mock_hub:
            mock_instance = Mock()
            mock_instance.reranker.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = RerankNode(
                name="empty_test",
                resource_key="bge-m3"
            )

            result = await node._process(
                query="test",
                documents=[],
                top_k=5,
                threshold=0.0
            )

            assert result == {"reranks": []}


class TestRerankNodeIntegration:
    """Integration tests for RerankNode with real ResourceHub."""

    @pytest.mark.asyncio
    async def test_rerank_node_with_hub(self, hub):
        """Test RerankNode works with ResourceHub."""
        from hush.providers.nodes import RerankNode
        from hush.core.states import StateSchema, MemoryState

        # Check if bge-m3-onnx reranker is available
        if not hub.has("reranking:bge-m3-onnx"):
            pytest.skip("reranking:bge-m3-onnx not configured in resources.yaml")

        node = RerankNode(
            name="rerank",
            resource_key="bge-m3-onnx"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "query": "What is machine learning?",
            "documents": [
                "Machine learning is a subset of artificial intelligence.",
                "The weather today is sunny.",
                "Deep learning uses neural networks."
            ],
            "top_k": 2
        })

        result = await node.run(state)

        assert "reranks" in result
        reranks = result["reranks"]
        assert len(reranks) == 2
        assert "score" in reranks[0]
        print(f"Reranked results (ONNX): {reranks}")

    @pytest.mark.asyncio
    async def test_rerank_node_with_pinecone(self, hub):
        """Test RerankNode works with Pinecone API."""
        from hush.providers.nodes import RerankNode
        from hush.core.states import StateSchema, MemoryState

        # Check if bge-m3 (Pinecone) reranker is available
        if not hub.has("reranking:bge-m3"):
            pytest.skip("reranking:bge-m3 not configured in resources.yaml")

        node = RerankNode(
            name="rerank_pinecone",
            resource_key="bge-m3"
        )

        schema = StateSchema(node=node)
        state = MemoryState(schema, inputs={
            "query": "What is machine learning?",
            "documents": [
                "Machine learning is a subset of artificial intelligence.",
                "The weather today is sunny.",
                "Deep learning uses neural networks."
            ],
            "top_k": 2
        })

        result = await node.run(state)

        assert "reranks" in result
        reranks = result["reranks"]
        assert len(reranks) == 2
        assert "score" in reranks[0]
        print(f"Reranked results (Pinecone): {reranks}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
