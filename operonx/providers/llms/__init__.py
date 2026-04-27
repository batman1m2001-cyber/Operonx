"""LLM providers for operonx workflows.

Light symbols (config classes, ``BaseLLM``, ``create_llm``,
``LLMGenerator``) are eager. Backend classes are lazy-loaded via
module-level ``__getattr__`` so this package can be imported with only
core dependencies — Gemini's ``google-cloud-aiplatform``, Anthropic's
``httpx``, etc. are only required when their backend is actually
accessed.
"""

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
from operonx.providers.llms.response import LLMGenerator

_LAZY_BACKENDS = {
    "OpenAISDKModel": ("operonx.providers.llms.openai", "providers"),
    "AzureSDKModel": ("operonx.providers.llms.azure", "providers"),
    "AnthropicModel": ("operonx.providers.llms.anthropic", "anthropic"),
    "GeminiOpenAISDKModel": ("operonx.providers.llms.gemini", "gemini"),
}


def __getattr__(name: str):
    if name in _LAZY_BACKENDS:
        module_path, extra = _LAZY_BACKENDS[name]
        try:
            import importlib

            module = importlib.import_module(module_path)
        except ImportError as exc:
            raise ImportError(
                f"{name} requires additional packages.\n"
                f"  Install with: pip install operonx[{extra}]\n"
                f"  Original error: {exc}"
            ) from exc
        cls = getattr(module, name)
        globals()[name] = cls
        return cls
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
