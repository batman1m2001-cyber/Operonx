# LLM Provider Abstraction

## Overview

`BaseLLM` là abstract class chuẩn hóa interface cho tất cả LLM providers (OpenAI, Azure, Gemini, vLLM).

Location: `hush-providers/hush/providers/llms/base.py`

## Class Definition

```python
class BaseLLM(ABC):
    """Base class for LLM implementations."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    @abstractmethod
    async def stream(
        self,
        messages: List[ChatCompletionMessageParam],
        temperature: float = 0.0,
        top_p: float = 0.1,
        n: Optional[int] = None,
        stop: Optional[Union[str, Sequence[str]]] = None,
        max_tokens: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        response_format: Optional[dict] = None,
        tools: Optional[dict] = None,
        **kwargs
    ) -> AsyncGenerator[ChatCompletionChunk, None]:
        """Stream responses từ LLM."""
        pass

    @abstractmethod
    async def generate(
        self,
        messages: List[ChatCompletionMessageParam],
        **kwargs
    ) -> ChatCompletion:
        """Generate complete response."""
        pass

    async def generate_batch(
        self,
        batch_messages: List[List[ChatCompletionMessageParam]],
        **kwargs
    ) -> List[ChatCompletion]:
        """Batch processing (optional override)."""
        raise NotImplementedError("Batch processing not implemented")
```

## Configuration

### LLMConfig Base

```python
class LLMConfig(YamlModel):
    _category: ClassVar[str] = "llm"

    api_type: LLMType  # openai, azure, gemini, vllm
    proxy: str | None = None
    cost_per_input_token: float | None = None
    cost_per_output_token: float | None = None
```

### Provider-Specific Configs

```python
# OpenAI / vLLM
class OpenAIConfig(LLMConfig):
    api_key: str
    base_url: str
    model: str
    batch_size: int = 50000
    batch_flush_interval: float = 60.0

# Azure
class AzureConfig(LLMConfig):
    api_key: str
    api_version: str
    azure_endpoint: str
    model: str

# Gemini
class GeminiConfig(LLMConfig):
    project_id: str
    private_key: str
    client_email: str
    location: str = "us-central1"
    model: str = "gemini-2.0-flash-001"
```

## Factory Pattern

```python
class LLMFactory:
    @staticmethod
    def create(config: LLMConfig) -> BaseLLM:
        if config.api_type in [LLMType.VLLM, LLMType.OPENAI]:
            from .openai import OpenAISDKModel
            return OpenAISDKModel(config=config)
        elif config.api_type == LLMType.AZURE:
            from .azure import AzureSDKModel
            return AzureSDKModel(config=config)
        elif config.api_type == LLMType.GEMINI:
            from .gemini import GeminiOpenAISDKModel
            return GeminiOpenAISDKModel(config=config)
        else:
            raise ValueError(f"Unsupported: {config.api_type}")
```

## Core Methods

### stream()

Streaming responses với AsyncGenerator:

```python
async def stream(
    self,
    messages: List[ChatCompletionMessageParam],
    temperature: float = 0.0,
    top_p: float = 0.1,
    max_tokens: Optional[int] = None,
    **kwargs
) -> AsyncGenerator[ChatCompletionChunk, None]:
    """
    Yields ChatCompletionChunk objects.

    Parameters:
        temperature: 0.0-2.0, lower = more deterministic
        top_p: 0.0-1.0, nucleus sampling
        max_tokens: limit output length
        stop: sequences to stop generation
        response_format: {"type": "json_object"} for JSON mode
        tools: function calling definitions
    """
```

### generate()

Complete response (non-streaming):

```python
async def generate(
    self,
    messages: List[ChatCompletionMessageParam],
    **kwargs
) -> ChatCompletion:
    """
    Returns complete ChatCompletion.
    Same parameters as stream().
    """
```

### generate_batch()

Parallel batch processing:

```python
async def generate_batch(
    self,
    batch_messages: List[List[ChatCompletionMessageParam]],
    **kwargs
) -> List[ChatCompletion]:
    """
    Process multiple conversations in parallel.
    Returns results in same order as input.
    """
```

## Helper Methods

### Image Handling

```python
def encode_image(self, image_path: str) -> Dict[str, Any]:
    """Convert local image to base64 data URL."""
    mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    with open(image_path, "rb") as f:
        base64_image = base64.b64encode(f.read()).decode('utf-8')
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}
    }

def resolve_image_paths(self, message: ChatCompletionMessageParam):
    """Auto-convert local paths to base64 in multimodal messages."""
```

### Parameter Preparation

```python
def _prepare_params(
    self,
    model: str,
    messages: List[ChatCompletionMessageParam],
    stream: bool,
    temperature: float,
    top_p: float,
    **kwargs
) -> Dict[str, Any]:
    """Prepare API params, filter None values, resolve images."""
    return {
        'model': model,
        'messages': [self.resolve_image_paths(msg) for msg in messages],
        'stream': stream,
        'temperature': temperature,
        'top_p': top_p,
        **{k: v for k, v in kwargs.items() if v is not None}
    }
```

## Supported Providers

| Provider | LLMType | Config Class | Features |
|----------|---------|--------------|----------|
| OpenAI | `OPENAI` | `OpenAIConfig` | Full support, Batch API |
| Azure | `AZURE` | `AzureConfig` | Full support |
| Gemini | `GEMINI` | `GeminiConfig` | Via OpenAI compatibility |
| vLLM | `VLLM` | `OpenAIConfig` | OpenAI-compatible |

## Usage

### Direct Factory

```python
from hush.providers.llms.factory import LLMFactory
from hush.providers.llms.config import OpenAIConfig, LLMType

config = OpenAIConfig(
    api_type=LLMType.OPENAI,
    api_key="sk-...",
    base_url="https://api.openai.com/v1",
    model="gpt-4"
)

llm = LLMFactory.create(config)

# Stream
async for chunk in llm.stream([{"role": "user", "content": "Hello"}]):
    print(chunk.choices[0].delta.content, end="")

# Generate
response = await llm.generate([{"role": "user", "content": "Hello"}])
print(response.choices[0].message.content)
```

### Via ResourceHub

```python
from hush.providers.registry import LLMPlugin
from hush.core.registry import get_hub

# Register plugin
LLMPlugin.register()

# Load from YAML
hub = get_hub()
llm = hub.llm("gpt-4")  # Loads configs/llm/gpt-4.yaml
```
