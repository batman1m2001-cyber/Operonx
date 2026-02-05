"""Test that all imports work correctly."""

def test_llm_imports():
    """Test LLM provider imports."""
    from hush.providers import (
        BaseLLM,
        LLMType,
        LLMConfig,
        OpenAIConfig,
        AzureConfig,
        GeminiConfig,
        LLMFactory,
    )
    assert BaseLLM is not None
    assert LLMType is not None
    assert LLMConfig is not None
    print("✓ LLM imports successful")


def test_embedding_imports():
    """Test embedding provider imports."""
    from hush.providers import (
        BaseEmbedder,
        EmbeddingType,
        EmbeddingConfig,
        EmbeddingFactory,
    )
    assert BaseEmbedder is not None
    assert EmbeddingType is not None
    assert EmbeddingConfig is not None
    print("✓ Embedding imports successful")


def test_reranker_imports():
    """Test reranker provider imports."""
    from hush.providers import (
        BaseReranker,
        RerankingType,
        RerankingConfig,
        RerankingFactory,
    )
    assert BaseReranker is not None
    assert RerankingType is not None
    assert RerankingConfig is not None
    print("✓ Reranker imports successful")


def test_node_imports():
    """Test node imports."""
    from hush.providers import (
        LLMNode,
        EmbeddingNode,
        RerankNode,
    )
    assert LLMNode is not None
    assert EmbeddingNode is not None
    assert RerankNode is not None
    print("✓ Node imports successful")


def test_config_creation():
    """Test configuration creation."""
    from hush.providers import LLMConfig, LLMType

    config_data = {
        "api_type": "openai",
        "api_key": "test-key",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4"
    }

    config = LLMConfig.create_config(config_data)
    assert config.api_type == LLMType.OPENAI
    assert config.api_key == "test-key"
    assert config.model == "gpt-4"
    print("✓ Config creation successful")


if __name__ == "__main__":
    print("\n" + "="*60)
    print(" Testing hush-providers imports ".center(60, "="))
    print("="*60 + "\n")

    test_llm_imports()
    test_embedding_imports()
    test_reranker_imports()
    test_node_imports()
    test_config_creation()

    print("\n" + "="*60)
    print(" All tests passed! ".center(60, "="))
    print("="*60)
