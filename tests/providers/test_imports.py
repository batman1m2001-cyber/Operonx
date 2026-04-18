"""Test that all provider imports resolve correctly and expose the expected interface."""

from operon.core.utils import YamlModel


def test_llm_imports():
    from operon.providers import BaseLLM, LLMConfig, LLMType

    assert hasattr(BaseLLM, "generate"), "BaseLLM must have generate()"
    assert hasattr(BaseLLM, "stream"), "BaseLLM must have stream()"
    assert LLMType.OPENAI.value == "openai"
    assert issubclass(LLMConfig, YamlModel)


def test_embedding_imports():
    from operon.providers import BaseEmbedder, EmbeddingConfig, EmbeddingType

    assert hasattr(BaseEmbedder, "run"), "BaseEmbedder must have run()"
    assert EmbeddingType.ONNX.value == "onnx"
    assert issubclass(EmbeddingConfig, YamlModel)


def test_reranker_imports():
    from operon.providers import BaseReranker, RerankingConfig, RerankingType

    assert hasattr(BaseReranker, "run"), "BaseReranker must have run()"
    assert RerankingType.ONNX.value == "onnx"
    assert issubclass(RerankingConfig, YamlModel)


def test_op_imports():
    from operon.core.ops import BaseOp

    from operon.providers import EmbeddingOp, LLMOp, RerankOp

    assert issubclass(LLMOp, BaseOp)
    assert issubclass(EmbeddingOp, BaseOp)
    assert issubclass(RerankOp, BaseOp)


def test_config_creation():
    from operon.providers import LLMConfig, LLMType

    config = LLMConfig.create_config(
        {
            "api_type": "openai",
            "api_key": "test-key",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4",
        }
    )
    assert config.api_type == LLMType.OPENAI
    assert config.api_key == "test-key"
    assert config.model == "gpt-4"
