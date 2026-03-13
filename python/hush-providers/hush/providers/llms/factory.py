"""Factory function for creating LLM backends."""

from hush.providers.llms.base import BaseLLM
from hush.providers.llms.config import LLMConfig, LLMType


def create_llm(config: LLMConfig) -> BaseLLM:
    """Create an LLM backend from config.

    Args:
        config: LLMConfig with api_type determining which backend to create.

    Returns:
        BaseLLM instance.

    Raises:
        ValueError: If api_type is unsupported.
        ImportError: If optional provider dependencies are missing.
    """
    if config.api_type in [LLMType.VLLM, LLMType.OPENAI]:
        from .openai import OpenAISDKModel

        model_class = OpenAISDKModel
    elif config.api_type == LLMType.AZURE:
        from .azure import AzureSDKModel

        model_class = AzureSDKModel
    elif config.api_type == LLMType.GEMINI:
        try:
            from .gemini import GeminiOpenAISDKModel

            model_class = GeminiOpenAISDKModel
        except ImportError as e:
            raise ImportError(
                "Gemini support requires google-cloud-aiplatform. "
                "Install it with: pip install hush-providers[gemini]"
            ) from e
    else:
        raise ValueError(f"Unsupported Model: {config.api_type}")

    return model_class(config=config)


async def main():
    llm = create_llm(LLMConfig.default())

    async for chunk in llm.stream(messages=[{"role": "user", "content": "Hello"}]):
        print(chunk)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
