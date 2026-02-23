"""Tests for provider node .of() classmethod shorthand (LLMOp.of, PromptOp.of, etc.)."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from hush.core.states import MemoryState, StateSchema

# ============================================================
# PromptOp.of() shorthand tests
# ============================================================


class TestPromptShorthand:
    """Test PromptOp.of() classmethod shorthand."""

    def test_prompt_shorthand_string_template(self):
        from hush.providers.ops.prompt import PromptOp

        node = PromptOp.of("Hello {user}", user="world")
        assert isinstance(node, PromptOp)
        assert "template" in node.inputs
        assert "user" in node.inputs

    def test_prompt_shorthand_dict_template(self):
        from hush.providers.ops.prompt import PromptOp

        node = PromptOp.of({"system": "You are {role}.", "user": "{query}"}, role="helpful")
        assert isinstance(node, PromptOp)
        assert "template" in node.inputs
        assert "role" in node.inputs

    def test_prompt_shorthand_with_outputs(self):
        from hush.core.ops.base import PARENT
        from hush.providers.ops.prompt import PromptOp

        node = PromptOp.of("Hello", outputs={"*": PARENT})
        assert "messages" in node.outputs

    def test_prompt_shorthand_auto_name(self):
        from hush.providers.ops.prompt import PromptOp

        my_prompt = PromptOp.of("Hello")
        assert my_prompt.name == "my_prompt"

    def test_prompt_shorthand_explicit_name(self):
        from hush.providers.ops.prompt import PromptOp

        node = PromptOp.of("Hello", name="chat_prompt")
        assert node.name == "chat_prompt"

    @pytest.mark.asyncio
    async def test_prompt_shorthand_execution(self):
        from hush.providers.ops.prompt import PromptOp

        node = PromptOp.of(
            {"system": "You are {role}.", "user": "Help with {task}."}, role="Claude", task="coding"
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)
        result = await node.run(state)

        messages = result["messages"]
        assert len(messages) == 2
        assert messages[0]["content"] == "You are Claude."
        assert messages[1]["content"] == "Help with coding."

    def test_prompt_shorthand_no_template(self):
        from hush.providers.ops.prompt import PromptOp

        node = PromptOp.of(conversation_history=[{"role": "user", "content": "Hi"}])
        assert isinstance(node, type(node))


# ============================================================
# LLMOp.of() shorthand tests
# ============================================================


class TestLLMShorthand:
    """Test LLMOp.of() classmethod shorthand."""

    def test_llm_shorthand_basic(self, hub):
        from hush.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of("gpt-4o", messages=[{"role": "user", "content": "Hi"}])
        assert isinstance(node, LLMOp)
        assert node.resource == "gpt-4o"
        assert "messages" in node.inputs

    def test_llm_shorthand_auto_name(self, hub):
        from hush.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        my_llm = LLMOp.of("gpt-4o", messages=[])
        assert my_llm.name == "my_llm"

    def test_llm_shorthand_explicit_name(self, hub):
        from hush.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of("gpt-4o", name="chat_llm", messages=[])
        assert node.name == "chat_llm"

    def test_llm_shorthand_with_outputs(self, hub):
        from hush.core.ops.base import PARENT
        from hush.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of("gpt-4o", messages=[], outputs={"*": PARENT})
        assert "content" in node.outputs

    def test_llm_shorthand_load_balancing(self, hub):
        from hush.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of(["gpt-4o", "gpt-4o"], ratios=[0.5, 0.5], messages=[])
        assert isinstance(node.resource, list)
        assert node.ratios == [0.5, 0.5]

    def test_llm_shorthand_with_fallback(self, hub):
        from hush.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of("gpt-4o", fallback=["gpt-4o"], messages=[])
        assert node.fallback == ["gpt-4o"]


# ============================================================
# EmbeddingOp.of() shorthand tests
# ============================================================


class TestEmbeddingShorthand:
    """Test EmbeddingOp.of() classmethod shorthand."""

    def test_embedding_shorthand_basic(self, hub):
        from hush.providers.ops.embedding import EmbeddingOp

        if not hub.has("embedding:bge-m3"):
            pytest.skip("embedding:bge-m3 not configured")

        node = EmbeddingOp.of("bge-m3", texts=["hello"])
        assert isinstance(node, EmbeddingOp)
        assert node.resource == "bge-m3"
        assert "texts" in node.inputs

    def test_embedding_shorthand_auto_name(self, hub):
        from hush.providers.ops.embedding import EmbeddingOp

        if not hub.has("embedding:bge-m3"):
            pytest.skip("embedding:bge-m3 not configured")

        my_embed = EmbeddingOp.of("bge-m3", texts=[])
        assert my_embed.name == "my_embed"

    def test_embedding_shorthand_with_outputs(self, hub):
        from hush.core.ops.base import PARENT
        from hush.providers.ops.embedding import EmbeddingOp

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
        from hush.providers.ops.rerank import RerankOp

        if not hub.has("reranking:bge-m3-onnx"):
            pytest.skip("reranking:bge-m3-onnx not configured")

        node = RerankOp.of("bge-m3-onnx", query="test", documents=["a", "b"])
        assert isinstance(node, RerankOp)
        assert node.resource == "bge-m3-onnx"
        assert "query" in node.inputs
        assert "documents" in node.inputs

    def test_rerank_shorthand_auto_name(self, hub):
        from hush.providers.ops.rerank import RerankOp

        if not hub.has("reranking:bge-m3-onnx"):
            pytest.skip("reranking:bge-m3-onnx not configured")

        my_rerank = RerankOp.of("bge-m3-onnx", query="q", documents=[])
        assert my_rerank.name == "my_rerank"

    def test_rerank_shorthand_with_outputs(self, hub):
        from hush.core.ops.base import PARENT
        from hush.providers.ops.rerank import RerankOp

        if not hub.has("reranking:bge-m3-onnx"):
            pytest.skip("reranking:bge-m3-onnx not configured")

        node = RerankOp.of("bge-m3-onnx", query="q", documents=[], outputs={"*": PARENT})
        assert "reranks" in node.outputs


# ============================================================
# ChainOp.of() shorthand tests
# ============================================================


def _mock_resource_hub():
    """Create a mock ResourceHub for ChainOp tests."""
    mock_hub = Mock()
    mock_hub.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
    return mock_hub


class TestLLMChainShorthand:
    """Test ChainOp.of() classmethod shorthand."""

    def test_llmchain_shorthand_basic(self):
        from hush.providers.ops.chain import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ChainOp.of("gpt-4", "Hello {user}", user="world")
            assert isinstance(node, ChainOp)
            assert node.resource == "gpt-4"
            assert "prompt" in node._ops
            assert "llm" in node._ops

    def test_llmchain_shorthand_dict_template(self):
        from hush.providers.ops.chain import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ChainOp.of(
                "gpt-4", {"system": "You are {role}.", "user": "{query}"}, role="helpful"
            )
            assert isinstance(node, ChainOp)
            assert "prompt" in node._ops

    def test_llmchain_shorthand_auto_name(self):
        from hush.providers.ops.chain import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            my_chain = ChainOp.of("gpt-4", "Hello")
            assert my_chain.name == "my_chain"

    def test_llmchain_shorthand_explicit_name(self):
        from hush.providers.ops.chain import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ChainOp.of("gpt-4", "Hello", name="chat_chain")
            assert node.name == "chat_chain"

    def test_llmchain_shorthand_with_outputs(self):
        from hush.core.ops.base import PARENT
        from hush.providers.ops.chain import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ChainOp.of("gpt-4", "Hello", outputs={"*": PARENT})
            assert "content" in node.outputs

    def test_llmchain_shorthand_extract(self):
        from hush.providers.ops.chain import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ChainOp.of(
                "gpt-4",
                "Classify: {text}",
                extract=["category: str", "confidence: float"],
                parser="xml",
                text="sample",
            )
            assert node.extract == ["category: str", "confidence: float"]
            assert node.parser == "xml"
            assert "parser" in node._ops

    def test_llmchain_shorthand_response_format(self):
        from hush.providers.ops.chain import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ChainOp.of(
                "gpt-4o",
                {"user": "Return JSON: {text}"},
                response_format={"type": "json_object"},
                text="sample",
            )
            assert node.response_format == {"type": "json_object"}

    def test_llmchain_shorthand_load_balancing(self):
        from hush.providers.ops.chain import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ChainOp.of(["gpt-4o", "gpt-4o-mini"], "Hello", ratios=[0.7, 0.3])
            assert isinstance(node.resource, list)
            assert node.ratios == [0.7, 0.3]

    def test_llmchain_shorthand_fallback(self):
        from hush.providers.ops.chain import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ChainOp.of("gpt-4o", "Hello", fallback=["claude-sonnet", "gpt-3.5-turbo"])
            assert node.fallback == ["claude-sonnet", "gpt-3.5-turbo"]

    def test_llmchain_shorthand_enable_thinking(self):
        from hush.providers.ops.chain import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ChainOp.of("gpt-4o", "Hello", enable_thinking=True)
            assert node.enable_thinking is True

    def test_llmchain_shorthand_template_variables(self):
        from hush.providers.ops.chain import ChainOp

        with patch("hush.providers.ops.llm.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ChainOp.of("gpt-4", "Hello {user}, do {task}", user="Alice", task="coding")
            # template vars should be in the chain's inputs
            assert "user" in node.inputs
            assert "task" in node.inputs
            assert "template" in node.inputs
