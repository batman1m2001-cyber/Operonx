"""SSE streaming handler: POST /path/stream -> text/event-stream."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Callable

from fastapi import Request
from fastapi.responses import StreamingResponse

from hush.core.streams import STREAM_SERVICE

if TYPE_CHECKING:
    from hush.serve.endpoint import Endpoint


def _extract_chunk_text(chunk) -> str:
    """Extract text content from an LLM streaming chunk."""
    if isinstance(chunk, dict):
        return chunk.get("content", "")
    if hasattr(chunk, "choices") and chunk.choices:
        delta = chunk.choices[0].delta
        if hasattr(delta, "content") and delta.content:
            return delta.content
    return ""


def create_stream_handler(endpoint: "Endpoint") -> Callable:
    """Create a FastAPI route handler for SSE streaming."""
    request_model = endpoint.request_model
    streaming_channels = endpoint.streaming_channels

    async def handler(body: request_model, request: Request):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        user_id = request.headers.get("X-User-ID")
        session_id = request.headers.get("X-Session-ID")

        result_future = asyncio.ensure_future(
            endpoint.engine.run(
                inputs=body.model_dump(exclude_none=False),
                request_id=request_id,
                user_id=user_id,
                session_id=session_id,
                tracer=endpoint.tracer,
            )
        )

        async def event_generator():
            if streaming_channels:
                channel = streaming_channels[0]
                async for chunk in STREAM_SERVICE.get(request_id, channel):
                    text = _extract_chunk_text(chunk)
                    if text:
                        yield f"data: {json.dumps({'content': text})}\n\n"

            result = await result_future
            output = {k: v for k, v in result.items() if not k.startswith("$")}
            yield f"event: done\ndata: {json.dumps(output)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Request-ID": request_id},
        )

    handler.__doc__ = f"Stream {endpoint.graph.name} workflow via SSE."
    return handler
