from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from openai.types.chat.chat_completion import ChatCompletion
from typing import List, AsyncGenerator, Union, Sequence, Optional

from hush.providers.llms.config import GeminiConfig
from .openai import OpenAISDKModel
import httpx
import asyncio
import os
import threading
import time
from datetime import datetime, timedelta
from google.oauth2 import service_account
from google.auth.transport.requests import Request
import requests
from hush.core.loggings import LOGGER


class GeminiOpenAISDKModel(OpenAISDKModel):
    """
    Simplified Gemini model implementation using Vertex AI's OpenAI-compatible endpoint.

    This class provides a streamlined interface to Google's Gemini models through
    Vertex AI, supporting both streaming and non-streaming chat completions with
    automatic token management and retry logic for reliability.

    Features:
        - Automatic Google Cloud authentication with retry logic
        - OpenAI SDK compatibility for easy integration
        - Optimized HTTP client configuration
        - Token refresh handling with expiration management
        - Support for multimodal inputs and tool calling

    Note:
        Requires valid Google Cloud credentials and appropriate Vertex AI permissions.
        SSL verification is disabled for environments with certificate issues.
    """

    def __init__(self, config: GeminiConfig):
        """
        Initialize the Gemini OpenAI SDK model with Google Cloud configuration.

        Sets up authentication credentials, HTTP client, and OpenAI client instance
        configured to work with Vertex AI's OpenAI-compatible endpoint.

        Args:
            config: GeminiConfig object containing Google Cloud project settings
                   including project_id, location, and service account credentials

        Raises:
            ValueError: If config is missing required fields (project_id, location)
            google.auth.exceptions.DefaultCredentialsError: If credentials are invalid

        Note:
            - HTTP client is configured with SSL verification disabled
            - Connection pooling is optimized for moderate concurrent usage
            - API key is set to placeholder and updated on first request
        """
        super().__init__(config)
        self.config = config

        # Setup credentials
        self.credentials = service_account.Credentials.from_service_account_info(
            self.config.__dict__,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )

        # HTTP client with basic settings
        self.http_client = httpx.AsyncClient(
            verify=False,
            timeout=30.0,
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=10
            )
        )

        # OpenAI client
        base_url = (f"https://{config.location}-aiplatform.googleapis.com"
                   f"/v1/projects/{config.project_id}"
                   f"/locations/{config.location}/endpoints/openapi")

        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key="placeholder",
            http_client=self.http_client
        )

    def _refresh_token(self) -> str:
        """
        Refresh Google Cloud access token with exponential backoff retry logic.

        Attempts to obtain a fresh OAuth2 access token from Google's authentication
        servers. Implements retry logic to handle intermittent network connectivity
        issues that commonly occur with oauth2.googleapis.com.

        Returns:
            str: Fresh OAuth2 access token for API authentication

        Raises:
            Exception: If token refresh fails after all retry attempts (3 total)
            google.auth.exceptions.RefreshError: If credentials are invalid or expired

        Note:
            - Uses exponential backoff: 1s, 2s, 4s between retries
            - SSL verification is disabled for environments with certificate issues
            - Session timeout is set to 30 seconds to prevent hanging
        """
        for attempt in range(3):
            try:
                # Simple session for auth requests
                session = requests.Session()
                session.verify = False
                session.timeout = 30

                self.credentials.refresh(Request(session=session))
                return self.credentials.token

            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                if attempt == 2:  # Last attempt
                    raise Exception(f"Token refresh failed after 3 attempts: {e}")

                wait_time = 2 ** attempt  # 1s, 2s, 4s
                print(f"Token refresh attempt {attempt + 1} failed, retrying in {wait_time}s...")
                time.sleep(wait_time)

    def _ensure_fresh_token(self):
        """
        Ensure the OpenAI client has a valid, non-expired authentication token.

        Checks the current token's expiration status and refreshes it if necessary.
        Uses a 5-minute buffer before actual expiration to prevent mid-request
        token expiration issues.

        Side Effects:
            - Updates self.client.api_key with fresh token if refresh is needed
            - May trigger network requests to Google's OAuth2 servers

        Raises:
            Exception: If token refresh fails (propagated from _refresh_token)

        Note:
            - Tokens are refreshed proactively 5 minutes before expiration
            - Called automatically before each API request
            - No-op if current token is still valid with sufficient time remaining
        """
        # Refresh if no token or expires within 5 minutes
        needs_refresh = (
            not self.credentials.token or
            not self.credentials.expiry or
            self.credentials.expiry < datetime.now() + timedelta(minutes=5)
        )

        if needs_refresh:
            self.client.api_key = self._refresh_token()

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
        """
        Stream chat completion responses from Gemini via Vertex AI OpenAI endpoint.

        Processes messages and streams responses from the Gemini model through
        Vertex AI's OpenAI-compatible API. Automatically handles authentication
        token refresh and supports all standard OpenAI streaming parameters.

        Args:
            messages: List of chat messages to process in the conversation.
                     Supports text and multimodal inputs including images.
            temperature: Controls randomness in response generation (0.0-2.0).
                        Lower values make output more focused and deterministic.
                        Default: 0.0 for consistent responses.
            top_p: Controls diversity via nucleus sampling (0.0-1.0).
                  Only tokens with top_p probability mass are considered.
                  Default: 0.1 for focused responses.
            n: Number of chat completion choices to generate for each input message.
               Only the first choice is typically used in streaming.
            stop: Up to 4 sequences where the API will stop generating further tokens.
                 Can be a string or list of strings.
            max_tokens: Maximum number of tokens to generate in the chat completion.
                       Total length is limited by model's context length.
            frequency_penalty: Penalizes new tokens based on their existing frequency
                             in the text so far (-2.0 to 2.0).
            presence_penalty: Penalizes new tokens based on whether they appear
                            in the text so far (-2.0 to 2.0).
            response_format: Object specifying the format that the model must output.
                           Compatible with structured output requirements.
            tools: Tool definitions for function calling capabilities.
                  Allows the model to call external functions during generation.
            **kwargs: Additional streaming options specific to the Vertex AI implementation

        Returns:
            AsyncGenerator yielding ChatCompletionChunk objects containing partial
                response data as it becomes available from the model

        Raises:
            Exception: If authentication token refresh fails after retries
            httpx.HTTPError: If API request fails due to network or server issues

        Note:
            - Authentication tokens are automatically refreshed before requests
            - Local image file paths are handled by the parent OpenAISDKModel class
            - Supports Gemini's advanced capabilities including multimodal inputs
        """
        # Ensure we have a fresh token
        self._ensure_fresh_token()
        async for chunk in super().stream(
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            n=n,
            stop=stop,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            response_format=response_format,
            tools=tools,
            **kwargs
        ):
            yield chunk

    async def generate(
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
    ) -> ChatCompletion:
        """
        Generate a complete chat completion response from Gemini via Vertex AI.

        Processes messages and returns a complete response from the Gemini model
        through Vertex AI's OpenAI-compatible API. This is the non-streaming
        version that waits for the full response before returning.

        Args:
            messages: List of chat messages to process in the conversation.
                     Supports text and multimodal inputs including images.
            temperature: Controls randomness in response generation (0.0-2.0).
                        Lower values make output more focused and deterministic.
                        Default: 0.0 for consistent responses.
            top_p: Controls diversity via nucleus sampling (0.0-1.0).
                  Only tokens with top_p probability mass are considered.
                  Default: 0.1 for focused responses.
            n: Number of chat completion choices to generate for each input message.
               Multiple choices will be returned in the response.
            stop: Up to 4 sequences where the API will stop generating further tokens.
                 Can be a string or list of strings.
            max_tokens: Maximum number of tokens to generate in the chat completion.
                       Total length is limited by model's context length.
            frequency_penalty: Penalizes new tokens based on their existing frequency
                             in the text so far (-2.0 to 2.0).
            presence_penalty: Penalizes new tokens based on whether they appear
                            in the text so far (-2.0 to 2.0).
            response_format: Object specifying the format that the model must output.
                           Compatible with structured output requirements.
            tools: Tool definitions for function calling capabilities.
                  Allows the model to call external functions during generation.
            **kwargs: Additional generation options specific to the Vertex AI implementation

        Returns:
            ChatCompletion: Complete response object containing the generated text,
                          usage statistics, and metadata from the model

        Raises:
            Exception: If authentication token refresh fails after retries
            httpx.HTTPError: If API request fails due to network or server issues

        Note:
            - Authentication tokens are automatically refreshed before requests
            - For long responses, consider using stream() method instead
            - Supports all Gemini model capabilities including function calling
        """
        # Ensure we have a fresh token
        self._ensure_fresh_token()

        return await super().generate(
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            n=n,
            stop=stop,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            response_format=response_format,
            tools=tools,
            **kwargs
        )


async def main():
    """Test the Gemini OpenAI SDK Model"""
    import os

    config = GeminiConfig(
        project_id=os.getenv("GOOGLE_CLOUD_PROJECT", "your-project-id"),
        location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        model="gemini-2.0-flash",
        service_account_file=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service-account.json")
    )

    model = GeminiOpenAISDKModel(config=config)

    async for chunk in model.stream(
        messages=[{"role": "user", "content": "say hi"}],
        status_code=200
    ):
        print(chunk)


async def test_tool_calling():
    import os
    import json

    config = GeminiConfig(
        project_id=os.getenv("GOOGLE_CLOUD_PROJECT", "your-project-id"),
        location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        model="gemini-2.0-flash",
        service_account_file=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service-account.json")
    )
    model = GeminiOpenAISDKModel(config)
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
    print(completion)

    # Text-only example with tools
    print("Text-only example with tools (streaming):")
    async for chunk in model.stream(
        messages=[{"role": "user", "content": "What's the weather like in San Francisco?"}],
        tools=tools
    ):
        if chunk.choices:
            content = chunk.choices[0].delta.content
            if content:
                pass

            tool_calls = chunk.choices[0].delta.tool_calls
            if tool_calls:
                print(tool_calls)


async def test_multimodal_image():
    import os

    config = GeminiConfig(
        project_id=os.getenv("GOOGLE_CLOUD_PROJECT", "your-project-id"),
        location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        model="gemini-2.0-flash",
        service_account_file=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service-account.json")
    )
    model = GeminiOpenAISDKModel(config)

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
    asyncio.run(test_tool_calling())
