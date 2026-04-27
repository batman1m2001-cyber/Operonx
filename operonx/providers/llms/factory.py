"""Factory function for creating LLM backends.

Each backend is lazy-imported inside its dispatch branch so that
``import operonx.providers.llms`` doesn't pull in optional dependencies
(``google-cloud-aiplatform`` for Gemini, ``boto3`` for Bedrock, etc.).
A user who only installed ``operonx[anthropic]`` can still load the
package without crashing — they only hit the missing-dep error if they
actually try to instantiate a different backend.
"""

from operonx.providers.llms.base import BaseLLM
from operonx.providers.llms.config import LLMConfig, LLMType


def create_llm(config: LLMConfig) -> BaseLLM:
    """Create an LLM backend from config.

    Args:
        config: LLMConfig with api_type determining which backend to create.

    Returns:
        BaseLLM instance.

    Raises:
        ValueError: If api_type is unsupported.
        ImportError: With a helpful pointer to the right ``operonx[<extra>]``
            install when an optional dependency is missing.
    """
    if config.api_type in [LLMType.VLLM, LLMType.OPENAI]:
        try:
            from .openai import OpenAISDKModel
        except ImportError as e:
            raise ImportError(_missing_extra_message("OpenAISDKModel", "providers", e)) from e
        return OpenAISDKModel(config=config)
    if config.api_type == LLMType.AZURE:
        try:
            from .azure import AzureSDKModel
        except ImportError as e:
            raise ImportError(_missing_extra_message("AzureSDKModel", "providers", e)) from e
        return AzureSDKModel(config=config)
    if config.api_type == LLMType.GEMINI:
        try:
            from .gemini import GeminiOpenAISDKModel
        except ImportError as e:
            raise ImportError(_missing_extra_message("Gemini", "gemini", e)) from e
        return GeminiOpenAISDKModel(config=config)
    if config.api_type == LLMType.ANTHROPIC:
        try:
            from .anthropic import AnthropicModel
        except ImportError as e:
            raise ImportError(_missing_extra_message("AnthropicModel", "anthropic", e)) from e
        return AnthropicModel(config=config)
    raise ValueError(f"Unsupported Model: {config.api_type}")


def _missing_extra_message(backend: str, extra: str, exc: ImportError) -> str:
    return (
        f"{backend} requires additional packages.\n"
        f"  Install with: pip install operonx[{extra}]\n"
        f"  Original error: {exc}"
    )
