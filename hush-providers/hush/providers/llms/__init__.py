"""LLM providers for hush workflows."""

from hush.providers.llms.base import BaseLLM
from hush.providers.llms.config import (
    LLMType,
    LLMConfig,
    OpenAIConfig,
    AzureConfig,
    GeminiConfig,
)
from hush.providers.llms.factory import LLMFactory
from hush.providers.llms.response import LLMGenerator
from hush.providers.llms.openai import OpenAISDKModel
from hush.providers.llms.azure import AzureSDKModel

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
    "LLMFactory",
    "LLMGenerator",
    "OpenAISDKModel",
    "AzureSDKModel",
    "GeminiOpenAISDKModel",
]
