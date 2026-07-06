from typing import Any, Dict, List

from openai import AsyncAzureOpenAI
from openai.types.chat import ChatCompletionMessageParam

from operonx.providers.llms.config import AzureConfig

from .base import create_http_client
from .openai import OpenAISDKModel


class AzureSDKModel(OpenAISDKModel):
    """Azure model using openai sdk with tool calls and multimodal support."""

    def __init__(self, config: AzureConfig):
        super().__init__(config)

        self.http_client = create_http_client(verify=False, proxy=config.proxy)
        # Initialize OpenAI client
        self.client = AsyncAzureOpenAI(
            azure_endpoint=config.azure_endpoint,
            api_key=config.api_key,
            api_version=config.api_version,
            http_client=self.http_client,
        )

    def _prepare_params(
        self,
        model: str,
        messages: List[ChatCompletionMessageParam],
        stream: bool,
        temperature: float,
        top_p: float,
        **kwargs,
    ) -> Dict[str, Any]:
        """Prepare API parameters for Azure OpenAI, filtering unsupported parameters."""

        # Azure OpenAI supported parameters
        AZURE_SUPPORTED_PARAMS = {
            "max_tokens",
            "frequency_penalty",
            "presence_penalty",
            "stop",
            "seed",
            "response_format",
            "tools",
            "tool_choice",
        }

        # Start with base parameters
        params = {
            "model": model,
            "messages": [self.resolve_image_paths(msg) for msg in messages],
            "stream": stream,
            "temperature": temperature,
            "top_p": top_p,
        }

        # Handle special parameter mappings
        processed_messages = list(params["messages"])

        # Handle system_prompt - inject as system message at the beginning
        if kwargs.get("system_prompt"):
            system_msg = {"role": "system", "content": kwargs["system_prompt"]}
            # Check if first message is already system, if so replace it
            if processed_messages and processed_messages[0].get("role") == "system":
                processed_messages[0] = system_msg
            else:
                processed_messages.insert(0, system_msg)
            params["messages"] = processed_messages

        # Handle stop_sequences -> stop
        if kwargs.get("stop_sequences"):
            params["stop"] = kwargs["stop_sequences"]

        # Handle json_schema in response_format
        if kwargs.get("json_schema"):
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": kwargs["json_schema"],
            }
        elif kwargs.get("response_format") == "json":
            params["response_format"] = {"type": "json_object"}
        elif kwargs.get("response_format"):
            # For other response formats, pass as is
            if isinstance(kwargs["response_format"], str):
                params["response_format"] = {"type": kwargs["response_format"]}
            else:
                params["response_format"] = kwargs["response_format"]

        # Add only supported parameters, filtering None values
        for key, value in kwargs.items():
            if key in AZURE_SUPPORTED_PARAMS and value is not None:
                params[key] = value

        return params
