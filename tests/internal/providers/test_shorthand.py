"""Tests for provider node .of() classmethod shorthand (LLMOp.of, EmbeddingOp.of, RerankOp.of)."""

import pytest

# ============================================================
# LLMOp.of() shorthand tests
# ============================================================


class TestLLMShorthand:
    """Test LLMOp.of() classmethod shorthand."""

    def test_llm_shorthand_basic(self, hub):
        from operonx.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of("gpt-4o", prompt="Hi")
        assert isinstance(node, LLMOp)
        assert node.resource == "gpt-4o"
        assert "prompt" in node.inputs

    def test_llm_shorthand_string_prompt_with_vars(self, hub):
        from operonx.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        # NB: kwargs like `name=` are reserved for op init (op name).
        # Use a non-reserved kwarg for template vars.
        node = LLMOp.of("gpt-4o", prompt="Hello {who}", who="Alice")
        assert "prompt" in node.inputs
        assert "who" in node.inputs

    def test_llm_shorthand_dict_prompt(self, hub):
        from operonx.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of(
            "gpt-4o",
            prompt={"system": "You are {role}.", "user": "{q}"},
            role="helpful",
            q="Hello",
        )
        assert "role" in node.inputs
        assert "q" in node.inputs

    def test_llm_shorthand_auto_name(self, hub):
        from operonx.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        my_llm = LLMOp.of("gpt-4o", prompt="Hi")
        assert my_llm.name == "my_llm"

    def test_llm_shorthand_explicit_name(self, hub):
        from operonx.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of("gpt-4o", name="chat_llm", prompt="Hi")
        assert node.name == "chat_llm"

    def test_llm_shorthand_with_outputs(self, hub):
        from operonx.core.ops.base import PARENT
        from operonx.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of("gpt-4o", prompt="Hi", outputs={"*": PARENT})
        assert "content" in node.outputs

    def test_llm_shorthand_load_balancing(self, hub):
        from operonx.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of(["gpt-4o", "gpt-4o"], ratios=[0.5, 0.5], prompt="Hi")
        assert isinstance(node.resource, list)
        assert node.ratios == [0.5, 0.5]

    def test_llm_shorthand_with_fallback(self, hub):
        from operonx.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of("gpt-4o", fallback=["gpt-4o"], prompt="Hi")
        assert node.fallback == ["gpt-4o"]


# ============================================================
# EmbeddingOp.of() shorthand tests
# ============================================================


class TestEmbeddingShorthand:
    """Test EmbeddingOp.of() classmethod shorthand."""

    def test_embedding_shorthand_basic(self, hub):
        from operonx.providers.ops.embedding import EmbeddingOp

        if not hub.has("embedding:bge-m3"):
            pytest.skip("embedding:bge-m3 not configured")

        node = EmbeddingOp.of("bge-m3", texts=["hello"])
        assert isinstance(node, EmbeddingOp)
        assert node.resource == "bge-m3"
        assert "texts" in node.inputs

    def test_embedding_shorthand_auto_name(self, hub):
        from operonx.providers.ops.embedding import EmbeddingOp

        if not hub.has("embedding:bge-m3"):
            pytest.skip("embedding:bge-m3 not configured")

        my_embed = EmbeddingOp.of("bge-m3", texts=[])
        assert my_embed.name == "my_embed"

    def test_embedding_shorthand_with_outputs(self, hub):
        from operonx.core.ops.base import PARENT
        from operonx.providers.ops.embedding import EmbeddingOp

        if not hub.has("embedding:bge-m3"):
            pytest.skip("embedding:bge-m3 not configured")

        node = EmbeddingOp.of("bge-m3", texts=[], outputs={"*": PARENT})
        assert "embeddings" in node.outputs


# ============================================================
# RerankOp.of() shorthand tests
# ============================================================


class TestRerankShorthand:
    """Test RerankOp.of() classmethod shorthand."""

    def test_rerank_shorthand_basic(self, hub):
        from operonx.providers.ops.rerank import RerankOp

        try:
            hub.reranker("bge-m3-onnx")
        except (KeyError, Exception):
            pytest.skip("reranking:bge-m3-onnx not available (model files missing)")

        node = RerankOp.of("bge-m3-onnx", query="test", documents=["a", "b"])
        assert isinstance(node, RerankOp)
        assert node.resource == "bge-m3-onnx"
        assert "query" in node.inputs
        assert "documents" in node.inputs

    def test_rerank_shorthand_auto_name(self, hub):
        from operonx.providers.ops.rerank import RerankOp

        try:
            hub.reranker("bge-m3-onnx")
        except (KeyError, Exception):
            pytest.skip("reranking:bge-m3-onnx not available (model files missing)")

        my_rerank = RerankOp.of("bge-m3-onnx", query="q", documents=[])
        assert my_rerank.name == "my_rerank"

    def test_rerank_shorthand_with_outputs(self, hub):
        from operonx.core.ops.base import PARENT
        from operonx.providers.ops.rerank import RerankOp

        try:
            hub.reranker("bge-m3-onnx")
        except (KeyError, Exception):
            pytest.skip("reranking:bge-m3-onnx not available (model files missing)")

        node = RerankOp.of("bge-m3-onnx", query="q", documents=[], outputs={"*": PARENT})
        assert "reranks" in node.outputs
