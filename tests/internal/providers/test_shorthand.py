"""Tests for provider node .of() classmethod shorthand (LLMOp.of, PromptOp.of, etc.)."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from operon.core.states import MemoryState, StateSchema

# ============================================================
# PromptOp.of() shorthand tests
# ============================================================


class TestPromptShorthand:
    """Test PromptOp.of() classmethod shorthand."""

    def test_prompt_shorthand_string_template(self):
        from operon.providers.ops.prompt import PromptOp

        node = PromptOp.of("Hello {user}", user="world")
        assert isinstance(node, PromptOp)
        assert "template" in node.inputs
        assert "user" in node.inputs

    def test_prompt_shorthand_dict_template(self):
        from operon.providers.ops.prompt import PromptOp

        node = PromptOp.of({"system": "You are {role}.", "user": "{query}"}, role="helpful")
        assert isinstance(node, PromptOp)
        assert "template" in node.inputs
        assert "role" in node.inputs

    def test_prompt_shorthand_with_outputs(self):
        from operon.core.ops.base import PARENT

        from operon.providers.ops.prompt import PromptOp

        node = PromptOp.of("Hello", outputs={"*": PARENT})
        assert "messages" in node.outputs

    def test_prompt_shorthand_auto_name(self):
        from operon.providers.ops.prompt import PromptOp

        my_prompt = PromptOp.of("Hello")
        assert my_prompt.name == "my_prompt"

    def test_prompt_shorthand_explicit_name(self):
        from operon.providers.ops.prompt import PromptOp

        node = PromptOp.of("Hello", name="chat_prompt")
        assert node.name == "chat_prompt"

    @pytest.mark.asyncio
    async def test_prompt_shorthand_execution(self):
        from operon.providers.ops.prompt import PromptOp

        node = PromptOp.of(
            {"system": "You are {role}.", "user": "Help with {task}."}, role="Claude", task="coding"
        )

        schema = StateSchema(op=node)
        state = MemoryState(schema)
        result = {}
        async for _, result in node.run(state):
            pass
        messages = result["messages"]
        assert len(messages) == 2
        assert messages[0]["content"] == "You are Claude."
        assert messages[1]["content"] == "Help with coding."

    def test_prompt_shorthand_no_template(self):
        from operon.providers.ops.prompt import PromptOp

        node = PromptOp.of(conversation_history=[{"role": "user", "content": "Hi"}])
        assert isinstance(node, type(node))


# ============================================================
# LLMOp.of() shorthand tests
# ============================================================


class TestLLMShorthand:
    """Test LLMOp.of() classmethod shorthand."""

    def test_llm_shorthand_basic(self, hub):
        from operon.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of("gpt-4o", messages=[{"role": "user", "content": "Hi"}])
        assert isinstance(node, LLMOp)
        assert node.resource == "gpt-4o"
        assert "messages" in node.inputs

    def test_llm_shorthand_auto_name(self, hub):
        from operon.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        my_llm = LLMOp.of("gpt-4o", messages=[])
        assert my_llm.name == "my_llm"

    def test_llm_shorthand_explicit_name(self, hub):
        from operon.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of("gpt-4o", name="chat_llm", messages=[])
        assert node.name == "chat_llm"

    def test_llm_shorthand_with_outputs(self, hub):
        from operon.core.ops.base import PARENT

        from operon.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of("gpt-4o", messages=[], outputs={"*": PARENT})
        assert "content" in node.outputs

    def test_llm_shorthand_load_balancing(self, hub):
        from operon.providers.ops.llm import LLMOp

        if not hub.has("llm:gpt-4o"):
            pytest.skip("llm:gpt-4o not configured")

        node = LLMOp.of(["gpt-4o", "gpt-4o"], ratios=[0.5, 0.5], messages=[])
        assert isinstance(node.resource, list)
        assert node.ratios == [0.5, 0.5]

    def test_llm_shorthand_with_fallback(self, hub):
        from operon.providers.ops.llm import LLMOp

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
        from operon.providers.ops.embedding import EmbeddingOp

        if not hub.has("embedding:bge-m3"):
            pytest.skip("embedding:bge-m3 not configured")

        node = EmbeddingOp.of("bge-m3", texts=["hello"])
        assert isinstance(node, EmbeddingOp)
        assert node.resource == "bge-m3"
        assert "texts" in node.inputs

    def test_embedding_shorthand_auto_name(self, hub):
        from operon.providers.ops.embedding import EmbeddingOp

        if not hub.has("embedding:bge-m3"):
            pytest.skip("embedding:bge-m3 not configured")

        my_embed = EmbeddingOp.of("bge-m3", texts=[])
        assert my_embed.name == "my_embed"

    def test_embedding_shorthand_with_outputs(self, hub):
        from operon.core.ops.base import PARENT

        from operon.providers.ops.embedding import EmbeddingOp

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
        from operon.providers.ops.rerank import RerankOp

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
        from operon.providers.ops.rerank import RerankOp

        try:
            hub.reranker("bge-m3-onnx")
        except (KeyError, Exception):
            pytest.skip("reranking:bge-m3-onnx not available (model files missing)")

        my_rerank = RerankOp.of("bge-m3-onnx", query="q", documents=[])
        assert my_rerank.name == "my_rerank"

    def test_rerank_shorthand_with_outputs(self, hub):
        from operon.core.ops.base import PARENT

        from operon.providers.ops.rerank import RerankOp

        try:
            hub.reranker("bge-m3-onnx")
        except (KeyError, Exception):
            pytest.skip("reranking:bge-m3-onnx not available (model files missing)")

        node = RerankOp.of("bge-m3-onnx", query="q", documents=[], outputs={"*": PARENT})
        assert "reranks" in node.outputs


# ============================================================
# chain() shorthand tests
# ============================================================


def _mock_resource_hub():
    """Create a mock ResourceHub for chain tests."""
    mock_hub = Mock()
    mock_hub.llm.return_value = Mock(generate=AsyncMock(), stream=AsyncMock())
    return mock_hub


class TestChainShorthand:
    """Test chain() factory function."""

    def test_chain_basic(self):
        from operon.core.ops import GraphOp

        from operon.providers.ops.chain import chat

        with patch("operon.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = chat(resource="gpt-4", template="Hello {user}", user="world")
            assert isinstance(node, GraphOp)
            assert "prompt" in node._ops
            assert "llm" in node._ops

    def test_chain_dict_template(self):
        from operon.providers.ops.chain import chat

        with patch("operon.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = chat(
                resource="gpt-4",
                template={"system": "You are {role}.", "user": "{query}"},
                role="helpful",
            )
            assert "prompt" in node._ops

    def test_chain_auto_name(self):
        from operon.providers.ops.chain import chat

        with patch("operon.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            my_chat = chat(resource="gpt-4", template="Hello")
            assert my_chat.name == "my_chat"

    def test_chain_explicit_name(self):
        from operon.providers.ops.chain import chat

        with patch("operon.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = chat(resource="gpt-4", template="Hello", name="my_named_chat")
            assert node.name == "my_named_chat"

    def test_chain_with_outputs(self):
        from operon.core.ops.base import PARENT

        from operon.providers.ops.chain import chat

        with patch("operon.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = chat(resource="gpt-4", template="Hello", outputs={"*": PARENT})
            assert "content" in node.outputs

    def test_chain_ask(self):
        from operon.providers.ops.chain import ask

        with patch("operon.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = ask(
                resource="gpt-4",
                template="Classify: {text}",
                fields=["category: str", "confidence: float"],
                parser="xml",
                text="sample",
            )
            assert "parser" in node._ops

    def test_chain_response_format(self):
        from operon.providers.ops.chain import chat

        with patch("operon.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = chat(
                resource="gpt-4o",
                template={"user": "Return JSON: {text}"},
                response_format={"type": "json_object"},
                text="sample",
            )
            llm_op = node._ops["llm"]
            assert "response_format" in llm_op.inputs

    def test_chain_load_balancing(self):
        from operon.providers.ops.chain import chat

        with patch("operon.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = chat(resource=["gpt-4o", "gpt-4o-mini"], template="Hello", ratios=[0.7, 0.3])
            llm_op = node._ops["llm"]
            assert isinstance(llm_op.resource, list)
            assert llm_op.ratios == [0.7, 0.3]

    def test_chain_fallback(self):
        from operon.providers.ops.chain import chat

        with patch("operon.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = chat(
                resource="gpt-4o",
                template="Hello",
                fallback=["claude-sonnet", "gpt-3.5-turbo"],
            )
            llm_op = node._ops["llm"]
            assert llm_op.fallback == ["claude-sonnet", "gpt-3.5-turbo"]

    def test_chain_template_variables(self):
        from operon.providers.ops.chain import chat

        with patch("operon.providers.ops._utils.ResourceHub") as mock_cls:
            mock_cls.instance.return_value = _mock_resource_hub()

            node = chat(
                resource="gpt-4", template="Hello {user}, do {task}", user="Alice", task="coding"
            )
            # template vars should be in the chain's inputs
            assert "user" in node.inputs
            assert "task" in node.inputs
            assert "template" in node.inputs
