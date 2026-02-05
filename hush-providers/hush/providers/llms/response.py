from typing import Optional, AsyncIterator, Union, Dict
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
import json
import aiohttp
import asyncio
import uuid
import time
from datetime import datetime
from openai import AsyncStream


class LLMGenerator:
    """Efficient LLM response generator with streaming support."""

    @staticmethod
    def parse(line: Union[str, Dict], **kwargs) -> Optional[ChatCompletionChunk]:
        """Parse a string into a ChatCompletionChunk."""
        if isinstance(line, Dict):
            line.update(**kwargs)
            return ChatCompletionChunk(**line)

        if not line or line.isspace():
            return None

        line = line.strip()

        try:
            if line.startswith('data: '):
                line = line[6:]
            if line == '[DONE]':
                return None

            data = json.loads(line)
            data.update(**kwargs)

            if not data.get('choices'):
                return None

            return ChatCompletionChunk(**data)
        except Exception:
            return None

    @staticmethod
    def make_chunk(
        content: str,
        model: str,
        chat_id: str = None,
        last=False
    ) -> ChatCompletionChunk:
        """Create a response chunk with given content."""
        if not chat_id:
            chat_id = f"chatcmpl-{str(uuid.uuid4())}"

        params = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": int(datetime.now().timestamp()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "" if last else content},
                    "finish_reason": "stop" if last else None
                }
            ]
        }
        return ChatCompletionChunk(**params)

    @staticmethod
    def should_filter(content: str) -> bool:
        """Check if content should be filtered."""
        return any('\u4e00' <= char <= '\u9fff' for char in content) if content else False

    @staticmethod
    async def process(
        streamline: AsyncIterator,
        model: str,
        delay: float = 0.0,
        **kwargs
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Process text stream into ChatCompletionChunks."""
        first_line = True
        chat_id = f"chat-{str(uuid.uuid4())}"

        async for line in streamline:
            if first_line:
                first_line = False
                try:
                    data = json.loads(line)
                    if data.get('object') == 'error':
                        error_msg = data.get('message', 'Sorry, service unavailable. Try again later.')

                        # Stream error response
                        for word in error_msg.split():
                            yield LLMGenerator.make_chunk(word + " ", model, chat_id)
                            await asyncio.sleep(delay)

                        yield LLMGenerator.make_chunk("", model, chat_id, last=True)
                        return
                except Exception:
                    pass

            # Process content
            chunk = LLMGenerator.parse(line, **kwargs)
            if chunk:
                if len(chunk.choices) > 0:
                    if hasattr(chunk.choices[0].delta, 'content'):
                        content = chunk.choices[0].delta.content
                        if not LLMGenerator.should_filter(content):
                            yield chunk
                            if delay > 0:
                                await asyncio.sleep(delay)
                else:
                    yield chunk

    @staticmethod
    async def simulate(
        message: str,
        model: str = "default",
        word_by_word: bool = True,
        delay: float = 0.01
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Simulate an LLM response stream for testing."""
        chat_id = f"chat-{str(uuid.uuid4())}"

        tokens = message.split() if word_by_word else message
        for token in tokens:
            content = token + " " if word_by_word else token
            yield LLMGenerator.make_chunk(content, model, chat_id)
            await asyncio.sleep(delay)

        yield LLMGenerator.make_chunk("", model, chat_id, last=True)


# Example adapter for HTTP responses
async def response_to_text(response: aiohttp.ClientResponse) -> AsyncIterator[str]:
    """Convert HTTP response to text stream."""
    async for line in response.content:
        yield line.decode('utf-8')


# Usage example
async def main():
    import os

    url = os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions")

    payload = json.dumps({
        "model": "gpt-4o",
        "messages": [
            {
            "role": "user",
            "content": "Hello!"
            }
        ],
        "temperature": 0.7,
        "max_tokens": 500,
        "stream": True
    })
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {os.getenv("OPENAI_API_KEY", "your-api-key")}'
    }

    # Configure timeouts
    timeout = aiohttp.ClientTimeout(
        total=None,  # No total timeout
        connect=5.0,  # Connection timeout
        sock_connect=5.0,  # Socket connect timeout
        sock_read=30.0  # Socket read timeout
    )

    # Example with API response
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=False),
        timeout=timeout
    ) as session:
        async with session.post(
            url=url,
            json=payload,
            headers=headers
        ) as response:
            async for chunk in response.content:
                print(chunk)


async def simple_test():
    import os

    # API configuration
    url = os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {os.getenv("OPENAI_API_KEY", "your-api-key")}'
    }
    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello!"}],
        "temperature": 0.7,
        "max_tokens": 500,
        "stream": True
    }
    # Get streaming response
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as response:
            # Convert bytes to text
            async def text_stream():
                async for line in response.content:
                    yield line.decode('utf-8')

            # Process with LLMGenerator
            async for chunk in LLMGenerator.process(text_stream(), "gpt-4o"):
                if hasattr(chunk.choices[0].delta, 'content') and chunk.choices[0].delta.content:
                    print(chunk.choices[0].delta.content, end="", flush=True)
    print()  # Final newline


if __name__ == "__main__":
    asyncio.run(simple_test())
