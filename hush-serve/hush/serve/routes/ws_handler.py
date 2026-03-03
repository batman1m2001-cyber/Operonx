"""WebSocket bidirectional handler: WS /path/ws."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Callable

from fastapi import WebSocket, WebSocketDisconnect

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


def create_ws_handler(endpoint: "Endpoint") -> Callable:
    """Create a WebSocket handler for bidirectional communication.

    Protocol:
      Client -> {"inputs": {...}}
      Server -> {"type": "chunk", "data": {"content": "..."}}
      Server -> {"type": "result", "data": {full output}}
      Client -> {"type": "close"}
    """
    request_model = endpoint.request_model
    streaming_channels = endpoint.streaming_channels

    async def handler(websocket: WebSocket):
        await websocket.accept()

        try:
            while True:
                raw = await websocket.receive_text()
                msg = json.loads(raw)

                if msg.get("type") == "close":
                    break

                inputs = msg.get("inputs", msg)
                request_id = str(uuid.uuid4())

                try:
                    validated = request_model(**inputs)
                except Exception as e:
                    await websocket.send_json({"type": "error", "data": {"message": str(e)}})
                    continue

                result_future = asyncio.ensure_future(
                    endpoint.engine.run(
                        inputs=validated.model_dump(exclude_none=False),
                        request_id=request_id,
                        tracer=endpoint.tracer,
                    )
                )

                if streaming_channels:
                    channel = streaming_channels[0]

                    async def stream_chunks():
                        async for chunk in STREAM_SERVICE.get(request_id, channel):
                            text = _extract_chunk_text(chunk)
                            if text:
                                await websocket.send_json(
                                    {"type": "chunk", "data": {"content": text}}
                                )

                    stream_task = asyncio.create_task(stream_chunks())
                    result = await result_future
                    await stream_task
                else:
                    result = await result_future

                output = {k: v for k, v in result.items() if not k.startswith("$")}
                await websocket.send_json({"type": "result", "data": output})

        except WebSocketDisconnect:
            pass

    handler.__doc__ = f"WebSocket for {endpoint.graph.name} workflow."
    return handler
