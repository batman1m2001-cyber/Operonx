from abc import ABC, abstractmethod
from typing import Dict, List, AsyncGenerator, Any, Union, Optional, Sequence
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessageParam
)
import time
import asyncio
import sys
import base64
import mimetypes

from hush.providers.llms.config import LLMConfig


def configure_event_loop():
    """Configure appropriate event loop policy based on platform."""
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    elif sys.platform.startswith(("linux", "darwin")):
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform}")


configure_event_loop()


class BaseLLM(ABC):
    """Base class for LLM implementations (OpenAI, Azure, Gemini, etc...)"""

    def __init__(self, config: LLMConfig) -> None:
        """Initialize LLM with configuration."""
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
        """Stream responses from LLM with performance tracking and configurable parameters.

        Args:
            messages: List of chat messages to process in the conversation
            temperature: Controls randomness in response generation (0.0-2.0).
                        Lower values make output more focused and deterministic.
            top_p: Controls diversity via nucleus sampling (0.0-1.0).
                Only tokens with top_p probability mass are considered.
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
                            Compatible with GPT-4 Turbo and gpt-3.5-turbo-1106.
            **kwargs: Additional streaming options specific to the implementation

        Returns:
            AsyncGenerator yielding ChatCompletionChunk objects containing partial
                response data as it becomes available
        """

    @abstractmethod
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
        """Generate a complete chat completion for a given conversation with configurable parameters.

        Args:
            messages: List of chat messages representing the input conversation to generate from
            temperature: Controls randomness in response generation (0.0-2.0).
                        Lower values make output more focused and deterministic.
            top_p: Controls diversity via nucleus sampling (0.0-1.0).
                Only tokens with top_p probability mass are considered.
            n: Number of chat completion choices to generate for each input message.
                Defaults to 1 if not specified.
            stop: Up to 4 sequences where the API will stop generating further tokens.
                Can be a string or list of strings.
            max_tokens: Maximum number of tokens to generate in the chat completion.
                    Total length is limited by model's context length.
            frequency_penalty: Penalizes new tokens based on their existing frequency
                            in the text so far (-2.0 to 2.0).
            presence_penalty: Penalizes new tokens based on whether they appear
                            in the text so far (-2.0 to 2.0).
            response_format: Object specifying the format that the model must output.
                            Compatible with GPT-4 Turbo and gpt-3.5-turbo-1106.
            **kwargs: Additional generation options specific to the implementation

        Returns:
            ChatCompletion: Complete model response containing the generated text,
            usage statistics, and metadata

        Raises:
            ConnectionError: If connection to the LLM service fails
            HTTPError: If the LLM service returns an error status
            ValueError: If parameters are outside valid ranges
        """

    async def generate_batch(
        self,
        batch_messages: List[List[ChatCompletionMessageParam]],
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
    ) -> List[ChatCompletion]:
        """Generate chat completions for multiple requests in parallel batches with configurable parameters.

        Args:
            batch_messages: List of message lists, where each inner list represents
                        a separate conversation to process in the batch
            temperature: Controls randomness in response generation (0.0-2.0).
                        Applied to all requests in the batch.
            top_p: Controls diversity via nucleus sampling (0.0-1.0).
                Applied to all requests in the batch.
            n: Number of chat completion choices to generate for each input message.
                Applied to all requests in the batch.
            stream: Whether to stream back partial progress. Should be False for batch processing.
            stop: Up to 4 sequences where the API will stop generating further tokens.
                Applied to all requests in the batch.
            max_tokens: Maximum number of tokens to generate in each chat completion.
                    Applied to all requests in the batch.
            frequency_penalty: Penalizes new tokens based on their existing frequency
                            in the text so far (-2.0 to 2.0). Applied to all requests.
            presence_penalty: Penalizes new tokens based on whether they appear
                            in the text so far (-2.0 to 2.0). Applied to all requests.
            response_format: Object specifying the format that the model must output.
                            Applied to all requests in the batch.
            **kwargs: Additional batch processing options specific to the implementation

        Returns:
            List[ChatCompletion]: List of complete model responses corresponding
            to each input conversation, maintaining the same order as input

        Raises:
            NotImplementedError: If the subclass doesn't implement batch processing
            ConnectionError: If connection to the LLM service fails
            HTTPError: If the LLM service returns an error status
            ValueError: If parameters are outside valid ranges or batch is empty
            TimeoutError: If batch processing exceeds timeout limits
        """
        raise NotImplementedError("Batch processing not implemented for this LLM provider")

    def encode_image(self, image_path: str) -> Dict[str, Any]:
        """
        Encode a local image file to base64 format for OpenAI API consumption.

        Reads an image file from the local filesystem, encodes it as base64, and
        formats it as a data URL suitable for OpenAI's vision-enabled models.

        Args:
            image_path: Path to the local image file

        Returns:
            Dict containing the properly formatted image_url structure:
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64,<encoded_data>"
                }
            }

        Raises:
            FileNotFoundError: If the image file doesn't exist
            IOError: If the file cannot be read
        """
        mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')

        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{base64_image}"
            }
        }

    def resolve_image_paths(self, message: ChatCompletionMessageParam) -> ChatCompletionMessageParam:
        """
        Convert local image file paths to base64 data URLs in chat completion messages.

        Processes multimodal message content to handle image references. Local file paths
        are automatically converted to base64-encoded data URLs, while existing HTTP/HTTPS
        URLs and data URLs are preserved unchanged. This enables seamless use of local
        images with OpenAI's vision-enabled models.

        Args:
            message: A chat completion message that may contain image references

        Returns:
            ChatCompletionMessageParam: The processed message. If no local image paths
            were found, returns the original message unchanged. If local paths were
            converted, returns a copy with updated content.

        Note:
            - Only processes messages with list-type content (multimodal messages)
            - String content and None content are returned unchanged
            - Preserves all non-image content items in their original form
            - Uses copy() to avoid mutating the original message when changes are made
        """
        content = message.get("content")

        # Skip processing if content is string or None
        if not isinstance(content, list):
            return message

        # Process multimodal content
        processed_content = []
        copy_trigger = False

        for item in content:
            if item.get("type") == "image_url":
                # Extract URL from nested structure
                image_url = item.get("image_url")
                url = image_url.get("url") if isinstance(image_url, dict) else image_url

                # Convert file paths to base64, keep URLs as-is
                if url and not url.startswith(("http://", "https://", "data:")):
                    processed_content.append(self.encode_image(url))
                    copy_trigger = True
                    continue

            # Keep original item (text or valid image URL)
            processed_content.append(item)

        # Only create copy if we made changes
        if copy_trigger:
            result = message.copy()
            result['content'] = processed_content
            return result

        return message

    def has_chinese_characters(self, text: str) -> bool:
        """
        Check if a text string contains Chinese (CJK) characters.

        Args:
            text: The text string to check

        Returns:
            bool: True if any characters fall within the CJK Unified Ideographs
                Unicode range (U+4E00 to U+9FFF), False otherwise
        """
        # Handle non-string types gracefully
        if not isinstance(text, str):
            return False

        return any('\u4e00' <= char <= '\u9fff' for char in text)

    def check_chinese_characters(
        self,
        response: Union[ChatCompletion, ChatCompletionChunk],
        raise_on_found: bool = False
    ) -> bool:
        """
        Check if a chat response contains Chinese (CJK) characters.

        Works with both ChatCompletionChunk (streaming) and ChatCompletion (non-streaming)
        responses by examining the appropriate content field.

        Args:
            response: Either a ChatCompletionChunk or ChatCompletion object
            raise_on_found: If True, raises ValueError when Chinese characters are found

        Returns:
            bool: True if Chinese characters are found, False otherwise

        Raises:
            ValueError: If raise_on_found=True and Chinese characters are detected
            AttributeError/IndexError: Returns False for any parsing errors or missing content
        """
        try:
            if not hasattr(response, 'choices') or not response.choices:
                return False

            choice = response.choices[0]

            # Handle streaming response (ChatCompletionChunk)
            if hasattr(choice, 'delta'):
                content = getattr(choice.delta, 'content', None)
            # Handle non-streaming response (ChatCompletion)
            elif hasattr(choice, 'message'):
                content = getattr(choice.message, 'content', None)
            else:
                return False

            # Handle different content types
            has_chinese = False

            if isinstance(content, str):
                has_chinese = self.has_chinese_characters(content)
            elif isinstance(content, list):
                # Handle multimodal content (list of content parts)
                for item in content:
                    if isinstance(item, dict) and item.get('type') == 'text':
                        text_content = item.get('text', '')
                        if self.has_chinese_characters(text_content):
                            has_chinese = True
                            break
            # If content is None or other type, has_chinese remains False

            if has_chinese and raise_on_found:
                raise ValueError("Response contains Chinese characters, which are not allowed")

            return has_chinese

        except (AttributeError, IndexError):
            return False

    def _prepare_params(
        self,
        model: str,
        messages: List[ChatCompletionMessageParam],
        stream: bool,
        temperature: float,
        top_p: float,
        **kwargs
    ) -> Dict[str, Any]:
        """Prepare API parameters, filtering None values and resolving image paths."""
        return {
            'model': model,
            'messages': [self.resolve_image_paths(msg) for msg in messages],
            'stream': stream,
            'temperature': temperature,
            'top_p': top_p,
            **{k: v for k, v in kwargs.items() if v is not None}
        }
