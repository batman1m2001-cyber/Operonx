from openai import AsyncAzureOpenAI
from hush.providers.llms.config import AzureConfig, LLMConfig
from .openai import OpenAISDKModel
from openai.types.chat import ChatCompletionMessageParam
from typing import List, Dict, Any
import httpx


class AzureSDKModel(OpenAISDKModel):
    """Azure model using openai sdk with tool calls and multimodal support."""

    def __init__(self, config: AzureConfig):
        super().__init__(config)

        # Configure HTTP client with timeout and no SSL verification
        self.http_client = httpx.AsyncClient(
            proxy=config.proxy,
            verify=False,
            timeout=httpx.Timeout(
                connect=10.0,
                read=120.0,
                write=10.0,
                pool=5.0
            ),
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=10
            )
        )
        # Initialize OpenAI client
        self.client = AsyncAzureOpenAI(
            azure_endpoint=config.azure_endpoint,
            api_key=config.api_key,
            api_version=config.api_version,
            http_client=self.http_client
        )

    def _prepare_params(
        self,
        model: str,
        messages: List[ChatCompletionMessageParam],
        stream: bool,
        temperature: float,
        top_p: float,
        **kwargs
    ) -> Dict[str, Any]:
        """Prepare API parameters for Azure OpenAI, filtering unsupported parameters."""

        # Azure OpenAI supported parameters
        AZURE_SUPPORTED_PARAMS = {
            'max_tokens',
            'frequency_penalty',
            'presence_penalty',
            'stop',
            'seed',
            'response_format',
            'tools',
            'tool_choice'
        }

        # Start with base parameters
        params = {
            'model': model,
            'messages': [self.resolve_image_paths(msg) for msg in messages],
            'stream': stream,
            'temperature': temperature,
            'top_p': top_p,
        }

        # Handle special parameter mappings
        processed_messages = list(params['messages'])

        # Handle system_prompt - inject as system message at the beginning
        if kwargs.get('system_prompt'):
            system_msg = {"role": "system", "content": kwargs['system_prompt']}
            # Check if first message is already system, if so replace it
            if processed_messages and processed_messages[0].get('role') == 'system':
                processed_messages[0] = system_msg
            else:
                processed_messages.insert(0, system_msg)
            params['messages'] = processed_messages

        # Handle stop_sequences -> stop
        if kwargs.get('stop_sequences'):
            params['stop'] = kwargs['stop_sequences']

        # Handle json_schema in response_format
        if kwargs.get('json_schema'):
            params['response_format'] = {
                "type": "json_schema",
                "json_schema": kwargs['json_schema']
            }
        elif kwargs.get('response_format') == 'json':
            params['response_format'] = {"type": "json_object"}
        elif kwargs.get('response_format'):
            # For other response formats, pass as is
            if isinstance(kwargs['response_format'], str):
                params['response_format'] = {"type": kwargs['response_format']}
            else:
                params['response_format'] = kwargs['response_format']

        # Add only supported parameters, filtering None values
        for key, value in kwargs.items():
            if key in AZURE_SUPPORTED_PARAMS and value is not None:
                params[key] = value

        return params


async def main():
    import os

    config = AzureConfig(
        api_key=os.getenv("AZURE_OPENAI_API_KEY", "your-api-key"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", "https://your-resource.openai.azure.com"),
        api_version="2024-02-15-preview",
        model="gpt-4o"
    )
    model = AzureSDKModel(config)

    async for chunk in model.stream(messages=[{"role": "user", "content": "Hello!"}]):
        print(chunk)


async def test_tools_calling():
    import os
    import json

    config = AzureConfig(
        api_key=os.getenv("AZURE_OPENAI_API_KEY", "your-api-key"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", "https://your-resource.openai.azure.com"),
        api_version="2024-02-15-preview",
        model="gpt-4o"
    )
    model = AzureSDKModel(config)

    # Define some example tools
    weather_tool = {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather in a given location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city and state, e.g. San Francisco, CA"
                    }
                },
                "required": ["location"]
            }
        }
    }

    calculator_tool = {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Perform a calculation",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "The mathematical expression to evaluate"
                    }
                },
                "required": ["expression"]
            }
        }
    }

    tools = [weather_tool, calculator_tool]

    # Text-only example with tools
    print("Text-only example with tools:")
    completion = await model.generate(
        messages=[{"role": "user", "content": "What's the weather like in San Francisco?"}],
        tools=tools
    )
    print(json.dumps(completion.model_dump(), indent=2))

    # Text-only example with tools
    print("Text-only (stream) example with tools:")
    stream_response = model.stream(
        messages=[{"role": "user", "content": "What's the weather like in San Francisco?"}],
        tools=tools
    )
    async for chunk in stream_response:
        print(chunk)


async def test_multimodal_image():
    import os

    config = AzureConfig(
        api_key=os.getenv("AZURE_OPENAI_API_KEY", "your-api-key"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", "https://your-resource.openai.azure.com"),
        api_version="2024-02-15-preview",
        model="gpt-4o"
    )
    model = AzureSDKModel(config)

    # Multimodal example
    print("\nMultimodal example:")
    completion = await model.generate(
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "What can you see in this image?"},
                {"type": "image_url", "image_url": {"url": "https://example.com/sample-image.png"}}
            ]
        }],
    )
    print(completion)

    print("\nMultimodal example (stream):")
    stream_response = model.stream(
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "What can you see in this image?"},
                {"type": "image_url", "image_url": {"url": "https://example.com/sample-image.png"}}
            ]
        }],
    )
    async for chunk in stream_response:
        print(chunk)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
