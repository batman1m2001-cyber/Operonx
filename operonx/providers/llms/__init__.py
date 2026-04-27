"""LLM providers for operonx workflows."""

from operonx.providers.llms.anthropic import AnthropicModel
from operonx.providers.llms.azure import AzureSDKModel
from operonx.providers.llms.base import BaseLLM
from operonx.providers.llms.config import (
    AnthropicConfig,
    AzureConfig,
    GeminiConfig,
    LLMConfig,
    LLMType,
    OpenAIConfig,
)
from operonx.providers.llms.factory import create_llm
from operonx.providers.llms.openai import OpenAISDKModel
from operonx.providers.llms.response import LLMGenerator


# Lazy import for Gemini to avoid requiring google-cloud-aiplatform
def __getattr__(name):
    if name == "GeminiOpenAISDKModel":
        try:
            from operonx.providers.llms.gemini import GeminiOpenAISDKModel

            return GeminiOpenAISDKModel
        except ImportError:
            raise ImportError(
                "Gemini support requires google-cloud-aiplatform. "
                "Install it with: pip install operonx-providers[gemini]"
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
