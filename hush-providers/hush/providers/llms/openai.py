from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from openai.types.chat.chat_completion import ChatCompletion
from typing import List, AsyncGenerator, Optional, Sequence, Union
from hush.providers.llms.config import LLMConfig, OpenAIConfig
from hush.core import LOGGER
from .base import BaseLLM
import httpx
import asyncio
import os

# Before importing OpenAI, add this:
import openai._base_client
openai._base_client.get_platform = lambda: "Windows"


class OpenAISDKModel(BaseLLM):
    """OpenAI model (SDK) with tool calls and multimodal support."""

    def __init__(self, config: OpenAIConfig):
        super().__init__(config)

        # Configure HTTP client with timeout and no SSL verification
        self.http_client = httpx.AsyncClient(
            # verify=False,
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
        if hasattr(config, 'base_url'):
            self.client = AsyncOpenAI(
                base_url=config.base_url,
                api_key=config.api_key,
                http_client=self.http_client
            )

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
        Stream chat completion responses with Chinese character filtering.

        Processes messages to handle local image paths, then streams responses
        from the OpenAI-compatible API. Automatically raises an exception if
        any response chunk contains Chinese characters.

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

        Raises:
            ValueError: If any response chunk contains Chinese characters

        Note:
            - Local image file paths are automatically converted to base64 data URLs
            - Chinese character detection covers CJK Unified Ideographs (U+4E00-U+9FFF)
        """
        # Prepare parameters with stream_options for token usage
        params = self._prepare_params(
            model=self.config.model,
            messages=messages,
            stream=True,
            stream_options={"include_usage": True},  # Always include token usage
            temperature=temperature,
            top_p=top_p,
            n=n,
            stop=stop,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            response_format=response_format,
            tools=tools,
            extra_body=kwargs
        )

        sleep = kwargs.pop("sleep", 0.0)
        stream_response = await self.client.chat.completions.create(**params)
        async for chunk in stream_response:
            if not self.check_chinese_characters(chunk, raise_on_found=True):
                # Update kwargs onto chunk
                for key, value in kwargs.items():
                    setattr(chunk, key, value)
                await asyncio.sleep(sleep)

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
        Generate a complete chat completion response with Chinese character filtering.

        Processes messages to handle local image paths, then generates a complete
        response from the OpenAI-compatible API. Automatically raises an exception
        if the response contains Chinese characters.

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
            ChatCompletion: The complete response from the API

        Raises:
            ValueError: If the response contains Chinese characters

        Note:
            - Local image file paths are automatically converted to base64 data URLs
            - Chinese character detection covers CJK Unified Ideographs (U+4E00-U+9FFF)
        """
        # Prepare parameters
        params = self._prepare_params(
            model=self.config.model,
            messages=messages,
            stream=False,
            temperature=temperature,
            top_p=top_p,
            n=n,
            stop=stop,
            max_tokens=max_tokens,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            response_format=response_format,
            tools=tools,
            extra_body=kwargs
        )

        completion = await self.client.chat.completions.create(**params)
        self.check_chinese_characters(completion, raise_on_found=True)

        # Update kwargs onto chunk
        for key, value in kwargs.items():
            setattr(completion, key, value)

        return completion

    async def transcribe(
        self,
        file_path: str,
        model: str = "whisper-1",
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        response_format: str = "text",
        temperature: Optional[float] = None,
        **kwargs
    ) -> Union[str, dict]:
        """
        Transcribe audio file using OpenAI Whisper API.

        Args:
            file_path: Path to the audio file to transcribe
            model: ID of the model to use (default: "whisper-1")
            language: Language of the input audio (ISO-639-1 format, e.g., "en", "vi")
            prompt: Optional text to guide the model's style or continue a previous segment
            response_format: Format of the transcript output ("text", "json", "verbose_json", "srt", "vtt")
            temperature: Sampling temperature between 0 and 1 (higher = more random)
            **kwargs: Additional parameters to pass to the API

        Returns:
            Union[str, dict]: Transcription result as string (for "text" format) or dict (for other formats)

        Raises:
            FileNotFoundError: If the audio file doesn't exist
            ValueError: If the response contains Chinese characters (when applicable)
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        with open(file_path, "rb") as audio_file:
            transcript = await self.client.audio.transcriptions.create(
                model=model,
                file=audio_file,
                language=language,
                prompt=prompt,
                response_format=response_format,
                temperature=temperature,
                **kwargs
            )

        return transcript

    async def close(self):
        """Clean up resources"""
        await self.client.close()
        await self.http_client.aclose()

    # =========================================================================
    # Batch API Methods
    # =========================================================================

    async def batch_create(
        self,
        requests: List[dict],
        metadata: Optional[dict] = None,
        completion_window: str = "24h"
    ) -> dict:
        """Create a batch job with multiple requests.

        Args:
            requests: List of request dicts, each containing:
                - custom_id: Unique identifier for the request
                - method: HTTP method (usually "POST")
                - url: API endpoint (e.g., "/v1/chat/completions")
                - body: Request body with messages, model, etc.
            metadata: Optional metadata for the batch job
            completion_window: Time window for completion ("24h")

        Returns:
            dict: Batch job info including batch_id, status, etc.
        """
        import json
        import tempfile

        # Create JSONL content
        jsonl_content = "\n".join(json.dumps(req) for req in requests)

        # Upload file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write(jsonl_content)
            temp_path = f.name

        try:
            with open(temp_path, 'rb') as f:
                file_response = await self.client.files.create(
                    file=f,
                    purpose="batch"
                )

            # Create batch job
            batch = await self.client.batches.create(
                input_file_id=file_response.id,
                endpoint="/v1/chat/completions",
                completion_window=completion_window,
                metadata=metadata
            )

            return batch.model_dump()

        finally:
            # Cleanup temp file
            import os as _os
            _os.unlink(temp_path)

    async def batch_status(self, batch_id: str) -> dict:
        """Check the status of a batch job.

        Args:
            batch_id: The batch job ID

        Returns:
            dict: Batch status info including status, request_counts, etc.
        """
        batch = await self.client.batches.retrieve(batch_id)
        return batch.model_dump()

    async def batch_retrieve(self, batch_id: str) -> List[dict]:
        """Retrieve results from a completed batch job.

        Args:
            batch_id: The batch job ID

        Returns:
            List[dict]: List of results, each containing custom_id and response
        """
        import json

        # Get batch status to get output file ID
        batch = await self.client.batches.retrieve(batch_id)

        if batch.status != "completed":
            raise ValueError(f"Batch not completed. Status: {batch.status}")

        if not batch.output_file_id:
            raise ValueError("No output file available")

        # Download output file
        file_response = await self.client.files.content(batch.output_file_id)
        content = file_response.text

        # Parse JSONL results
        results = []
        for line in content.strip().split("\n"):
            if line:
                results.append(json.loads(line))

        return results

    async def batch_cancel(self, batch_id: str) -> dict:
        """Cancel a batch job.

        Args:
            batch_id: The batch job ID

        Returns:
            dict: Updated batch info
        """
        batch = await self.client.batches.cancel(batch_id)
        return batch.model_dump()

    async def submit_batch(
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
    ) -> dict:
        """Submit batch job and return immediately (non-blocking).

        Use this for fire-and-forget batch jobs. Check status later with
        batch_status() and retrieve results with batch_retrieve().

        Args:
            batch_messages: List of message lists, each representing a conversation
            temperature: Controls randomness (0.0-2.0)
            top_p: Nucleus sampling threshold (0.0-1.0)
            **kwargs: Additional parameters

        Returns:
            dict: Batch info including 'id' (batch_id) for later retrieval
                  Keys: id, status, request_counts, created_at, etc.
        """
        import uuid

        # Build batch requests
        requests = []
        for idx, messages in enumerate(batch_messages):
            custom_id = f"request-{idx}-{uuid.uuid4().hex[:8]}"
            body = {
                "model": self.config.model,
                "messages": [self.resolve_image_paths(msg) for msg in messages],
                "temperature": temperature,
                "top_p": top_p,
            }
            if n is not None:
                body["n"] = n
            if stop is not None:
                body["stop"] = stop
            if max_tokens is not None:
                body["max_tokens"] = max_tokens
            if frequency_penalty is not None:
                body["frequency_penalty"] = frequency_penalty
            if presence_penalty is not None:
                body["presence_penalty"] = presence_penalty
            if response_format is not None:
                body["response_format"] = response_format
            if tools is not None:
                body["tools"] = tools

            requests.append({
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": body
            })

        # Create and return batch job info immediately
        batch_info = await self.batch_create(requests)
        LOGGER.warning(f"Batch {batch_info['id']} | Submitted {len(requests)} requests")
        return batch_info

    async def wait_for_batch(
        self,
        batch_id: str,
        poll_interval: float = 10.0,
        timeout: float = 86400.0
    ) -> List[ChatCompletion]:
        """Wait for a batch job to complete and retrieve results.

        Use this after submit_batch() to wait and get results.

        Args:
            batch_id: The batch ID from submit_batch()
            poll_interval: Seconds between status checks
            timeout: Maximum wait time in seconds

        Returns:
            List[ChatCompletion]: Results in order of submission
        """
        import time

        start_time = time.time()

        while True:
            status = await self.batch_status(batch_id)
            batch_status = status["status"]
            request_counts = status.get("request_counts", {})
            completed = request_counts.get("completed", 0)
            total = request_counts.get("total", 0)
            failed = request_counts.get("failed", 0)
            elapsed = time.time() - start_time

            LOGGER.warning(
                f"Batch {batch_id} | Status: {batch_status} | "
                f"Progress: {completed}/{total} | "
                f"Failed: {failed} | Elapsed: {elapsed:.1f}s"
            )

            if batch_status == "completed":
                LOGGER.warning(f"Batch {batch_id} | Completed in {elapsed:.1f}s")
                break
            elif batch_status in ("failed", "expired", "cancelled"):
                raise RuntimeError(f"Batch job {batch_status}: {status}")

            if elapsed > timeout:
                await self.batch_cancel(batch_id)
                raise TimeoutError(f"Batch job timed out after {timeout}s")

            await asyncio.sleep(poll_interval)

        # Retrieve and parse results
        results = await self.batch_retrieve(batch_id)

        # Convert to ChatCompletion objects
        ordered_results = []
        for result in sorted(results, key=lambda r: r["custom_id"]):
            if result.get("response", {}).get("body"):
                completion = ChatCompletion.model_validate(result["response"]["body"])
                ordered_results.append(completion)
            elif result.get("error"):
                raise RuntimeError(f"Request {result['custom_id']} failed: {result['error']}")

        return ordered_results

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
        poll_interval: float = 10.0,
        timeout: float = 86400.0,
        **kwargs
    ) -> List[ChatCompletion]:
        """Generate completions for multiple message lists using Batch API.

        Convenience method that submits and waits for results.
        Uses submit_batch() + wait_for_batch() internally.

        Args:
            batch_messages: List of message lists, each representing a conversation
            temperature: Controls randomness (0.0-2.0)
            top_p: Nucleus sampling threshold (0.0-1.0)
            poll_interval: Seconds between status checks (default: 10.0)
            timeout: Maximum wait time in seconds (default: 86400 = 24h)
            **kwargs: Additional parameters

        Returns:
            List[ChatCompletion]: Results in same order as input
        """
        batch_info = await self.submit_batch(
            batch_messages=batch_messages,
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
        return await self.wait_for_batch(
            batch_id=batch_info["id"],
            poll_interval=poll_interval,
            timeout=timeout
        )


async def main():
    import os
    model = OpenAISDKModel(OpenAIConfig(
        api_key=os.getenv("OPENAI_API_KEY", "your-api-key-here"),
        base_url="https://api.openai.com/v1",
        model="gpt-4o"
    ))

    # response = await model.generate(messages=[{"role": "user", "content": "xin chào"}])
    # print(response)

    async for chunk in model.stream(
        messages=[{"role": "user", "content": "say hi"}],
        # status_code=200
    ):
        print(chunk)


async def test_tool_callings():
    import json
    import os

    model = OpenAISDKModel(OpenAIConfig(
        api_key=os.getenv("OPENROUTER_API_KEY", "your-api-key-here"),
        base_url="https://openrouter.ai/api/v1",
        model="qwen/qwen3-30b-a3b-instruct-2507"
    ))
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


async def test_time_to_first_token():
    """Test time to first token latency for the OpenAI SDK model."""
    import time

    print("Starting Time to First Token Test...")

    # Initialize model
    model_init_start = time.perf_counter()
    config = OpenAIConfig(
        api_key=os.getenv("OPENAI_API_KEY", "your-api-key-here"),
        base_url="https://api.openai.com/v1",
        model="gpt-4o"
    )
    model = OpenAISDKModel(config=config)
    model_init_time = time.perf_counter() - model_init_start
    print(f"Model initialization time: {model_init_time:.3f}s")

    query = """Summarize this text in one sentence: Machine learning is a subset of artificial intelligence that enables systems to learn and improve from experience without being explicitly programmed."""

    # Test message
    messages = [{"role": "user", "content": query}]

    # Measure time to first token
    start_time = time.perf_counter()
    first_token_time = None

    try:
        async for chunk in model.stream(messages=messages):
            if first_token_time is None:
                first_token_time = time.perf_counter() - start_time
                print(f"Time to First Token: {first_token_time:.3f}s")

            # Print first chunk content if available
            if chunk.choices and chunk.choices[0].delta.content:
                print(chunk.choices[0].delta.content, end="")

    except Exception as e:
        print(f"Error during streaming: {e}")

    finally:
        await model.close()

    return first_token_time


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
