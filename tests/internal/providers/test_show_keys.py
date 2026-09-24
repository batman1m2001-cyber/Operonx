"""show_keys defaults of the provider ops."""

from operonx.providers.ops import DocFetchOp, EmbeddingOp, LLMOp, RerankOp, VectorSearchOp


class TestLLMOp:
    def test_default_is_content(self):
        node = LLMOp(resource="r", inputs={"prompt": "hi"})
        assert node.show_keys == ("content",)

    def test_extracted_fields_are_the_answer(self):
        llm = LLMOp(resource="r", inputs={"prompt": "hi"}, fields=["intent: str", "reason: str"])
        assert llm.show_keys == ("intent", "reason")

    def test_declared_beats_the_fields(self):
        llm = LLMOp(
            resource="r", inputs={"prompt": "hi"}, fields=["intent: str"], show_keys="content"
        )
        assert llm.show_keys == ("content",)


class TestOtherProviders:
    def test_defaults(self):
        node = EmbeddingOp(resource="e", inputs={"texts": ["x"]})
        assert node.show_keys == ("embeddings",)
        node = RerankOp(resource="r", inputs={"query": "q", "documents": []})
        assert node.show_keys == ("reranks",)
        node = VectorSearchOp(resource="v", inputs={"query_vector": [0.0]})
        assert node.show_keys == ("ids", "scores")
        node = DocFetchOp(resource="d", inputs={"ids": ["1"]})
        assert node.show_keys == ("rows",)
