"""Tests for RerankOp functionality."""

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestRerankOp:
    """Tests for RerankOp."""

    def test_import(self):
        """Test RerankOp can be imported."""
        from operon.providers.ops import RerankOp

        assert RerankOp is not None

    def test_node_type(self):
        """Test RerankOp has correct type."""
        from operon.providers.ops import RerankOp

        with patch("operon.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.reranker.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = RerankOp(name="test_rerank", resource="bge-m3")

            assert node.type == "rerank"
            assert node.resource == "bge-m3"

    def test_input_schema(self):
        """Test RerankOp has query and documents inputs."""
        from operon.providers.ops import RerankOp

        with patch("operon.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.reranker.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = RerankOp(name="input_test", resource="bge-m3")

            assert "query" in node.inputs
            assert "documents" in node.inputs
            assert "top_k" in node.inputs
            assert "threshold" in node.inputs

    def test_output_schema(self):
        """Test RerankOp has reranks output."""
        from operon.providers.ops import RerankOp

        with patch("operon.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.reranker.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = RerankOp(name="output_test", resource="bge-m3")

            assert "reranks" in node.outputs

    def test_metadata(self):
        """Test specific_metadata returns model info."""
        from operon.providers.ops import RerankOp

        with patch("operon.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.reranker.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = RerankOp(name="metadata_test", resource="bge-m3")

            metadata = node.specific_metadata
            assert metadata["model"] == "bge-m3"

    @pytest.mark.asyncio
    async def test_process_string_documents(self):
        """Test processing list of string documents."""
        from operon.providers.ops import RerankOp

        with patch("operon.providers.ops._utils.ResourceHub") as mock_hub:
            mock_reranker = Mock()
            mock_reranker.run = AsyncMock(
                return_value=[{"index": 1, "score": 0.9}, {"index": 0, "score": 0.7}]
            )
            mock_instance = Mock()
            mock_instance.get.return_value = mock_reranker
            mock_hub.instance.return_value = mock_instance

            node = RerankOp(name="process_test", resource="bge-m3")

            result = await node._process(
                query="test query", documents=["doc1", "doc2"], top_k=2, threshold=0.0
            )

            assert "reranks" in result
            assert len(result["reranks"]) == 2
            assert result["reranks"][0]["score"] == 0.9

    @pytest.mark.asyncio
    async def test_process_dict_documents(self):
        """Test processing list of dict documents."""
        from operon.providers.ops import RerankOp

        with patch("operon.providers.ops._utils.ResourceHub") as mock_hub:
            mock_reranker = Mock()
            mock_reranker.run = AsyncMock(return_value=[{"index": 0, "score": 0.95}])
            mock_instance = Mock()
            mock_instance.get.return_value = mock_reranker
            mock_hub.instance.return_value = mock_instance

            node = RerankOp(name="dict_test", resource="bge-m3")

            result = await node._process(
                query="test",
                documents=[{"id": 1, "content": "doc1"}, {"id": 2, "content": "doc2"}],
                top_k=1,
                threshold=0.0,
            )

            assert "reranks" in result
            assert "id" in result["reranks"][0]
            assert "score" in result["reranks"][0]

    @pytest.mark.asyncio
    async def test_process_empty_documents(self):
        """Test processing empty document list."""
        from operon.providers.ops import RerankOp

        with patch("operon.providers.ops._utils.ResourceHub") as mock_hub:
            mock_instance = Mock()
            mock_instance.reranker.return_value = Mock(run=AsyncMock())
            mock_hub.instance.return_value = mock_instance

            node = RerankOp(name="empty_test", resource="bge-m3")

            result = await node._process(query="test", documents=[], top_k=5, threshold=0.0)

            assert result == {"reranks": []}


class TestRerankOpIntegration:
    """Integration tests for RerankOp with real ResourceHub."""

    @pytest.mark.asyncio
    async def test_rerank_node_with_hub(self, hub):
        """Test RerankOp works with ResourceHub."""
        from operon.core.states import MemoryState, StateSchema

        from operon.providers.ops import RerankOp

        # Skip cleanly if the ONNX runtime deps aren't installed. BaseOp.run()
        # swallows init errors and logs them, so the assertion below would
        # otherwise fail with a misleading "reranks not in result" message.
        pytest.importorskip("onnxruntime")
        pytest.importorskip("tokenizers")

        # Check if bge-m3-onnx reranker is available
        if not hub.has("reranking:bge-m3-onnx"):
            pytest.skip("reranking:bge-m3-onnx not configured in resources.yaml")

        try:
            node = RerankOp(name="rerank", resource="bge-m3-onnx")
        except (KeyError, ImportError):
            pytest.skip("reranking:bge-m3-onnx failed to initialize (missing dependencies)")

        schema = StateSchema(op=node)
        state = MemoryState(
            schema,
            inputs={
                "query": "What is machine learning?",
                "documents": [
                    "Machine learning is a subset of artificial intelligence.",
                    "The weather today is sunny.",
                    "Deep learning uses neural networks.",
                ],
                "top_k": 2,
            },
        )

        result = {}

        async for _, result in node.run(state):
            pass
        assert "reranks" in result
        reranks = result["reranks"]
        assert len(reranks) == 2
        assert "score" in reranks[0]
        print(f"Reranked results (ONNX): {reranks}")

    @pytest.mark.asyncio
    async def test_rerank_node_with_pinecone(self, hub):
        """Test RerankOp works with Pinecone API."""
        from operon.core.states import MemoryState, StateSchema

        from operon.providers.ops import RerankOp

        # Check if bge-m3 (Pinecone) reranker is available
        if not hub.has("reranking:bge-m3"):
            pytest.skip("reranking:bge-m3 not configured in resources.yaml")

        node = RerankOp(name="rerank_pinecone", resource="bge-m3")

        schema = StateSchema(op=node)
        state = MemoryState(
            schema,
            inputs={
                "query": "What is machine learning?",
                "documents": [
                    "Machine learning is a subset of artificial intelligence.",
                    "The weather today is sunny.",
                    "Deep learning uses neural networks.",
                ],
                "top_k": 2,
            },
        )

        result = {}

        async for _, result in node.run(state):
            pass
        assert "reranks" in result
        reranks = result["reranks"]
        assert len(reranks) == 2
        assert "score" in reranks[0]
        print(f"Reranked results (Pinecone): {reranks}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
