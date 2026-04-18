"""LLM providers for operon workflows."""

from operon.providers.llms.anthropic import AnthropicModel
from operon.providers.llms.azure import AzureSDKModel
from operon.providers.llms.base import BaseLLM
from operon.providers.llms.config import (
    AnthropicConfig,
    AzureConfig,
    GeminiConfig,
    LLMConfig,
    LLMType,
    OpenAIConfig,
)
from operon.providers.llms.factory import create_llm
from operon.providers.llms.openai import OpenAISDKModel
from operon.providers.llms.response import LLMGenerator


# Lazy import for Gemini to avoid requiring google-cloud-aiplatform
def __getattr__(name):
    if name == "GeminiOpenAISDKModel":
        try:
            from operon.providers.llms.gemini import GeminiOpenAISDKModel

            return GeminiOpenAISDKModel
        except ImportError:
            raise ImportError(
                "Gemini support requires google-cloud-aiplatform. "
                "Install it with: pip install operon-providers[gemini]"
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
