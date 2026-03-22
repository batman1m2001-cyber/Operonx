"""LLM providers for hush workflows."""

from hush.providers.llms.anthropic import AnthropicModel
from hush.providers.llms.azure import AzureSDKModel
from hush.providers.llms.base import BaseLLM
from hush.providers.llms.config import (
    AnthropicConfig,
    AzureConfig,
    GeminiConfig,
    LLMConfig,
    LLMType,
    OpenAIConfig,
)
from hush.providers.llms.factory import create_llm
from hush.providers.llms.openai import OpenAISDKModel
from hush.providers.llms.response import LLMGenerator


# Lazy import for Gemini to avoid requiring google-cloud-aiplatform
def __getattr__(name):
    if name == "GeminiOpenAISDKModel":
        try:
            from hush.providers.llms.gemini import GeminiOpenAISDKModel

            return GeminiOpenAISDKModel
        except ImportError:
            raise ImportError(
                "Gemini support requires google-cloud-aiplatform. "
                "Install it with: pip install hush-providers[gemini]"
            )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BaseLLM",
    "LLMType",
    "LLMConfig",
    "OpenAIConfig",
    "AzureConfig",
    "GeminiConfig",
    "AnthropicConfig",
    "AnthropicModel",
    "create_llm",
    "LLMGenerator",
    "OpenAISDKModel",
    "AzureSDKModel",
    "GeminiOpenAISDKModel",
]
