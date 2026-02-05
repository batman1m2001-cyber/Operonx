from hush.providers.llms.base import BaseLLM
from hush.providers.llms.config import LLMConfig, LLMType


class LLMFactory:
    r"""Factory class for llm backends."""

    @staticmethod
    def create(
        config: LLMConfig,
    ) -> BaseLLM:
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

        return model_class(
            config=config
        )


async def main():
    llm = LLMFactory.create(LLMConfig.default())

    async for chunk in llm.stream(
        messages=[{"role": "user", "content": "Hello"}]
    ):
        print(chunk)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
