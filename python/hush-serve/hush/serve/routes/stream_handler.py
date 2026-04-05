"""SSE streaming handler: POST /path/stream -> text/event-stream.

Uses engine.start() to deliver real-time token events from generator ops,
followed by a final "done" event with the complete result.
"""

import json
import uuid
from typing import Callable

from fastapi import Request
from fastapi.responses import StreamingResponse

from hush.serve.schema import strip_internal_keys


def create_stream_handler(endpoint) -> Callable:
    """Create a FastAPI route handler for SSE streaming."""
    request_model = endpoint.request_model

    async def handler(body: request_model, request: Request):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        user_id = request.headers.get("X-User-ID")
        session_id = request.headers.get("X-Session-ID")

        async def event_generator():
            handle = endpoint.engine.start(
                inputs=body.model_dump(exclude_none=False),
                request_id=request_id,
                user_id=user_id,
                session_id=session_id,
            )

            async for _op, _ctx, data in handle:
                yield f"event: token\ndata: {json.dumps(data)}\n\n"

            # Build final result from buffered frames (non-consuming)
            output = await handle.result()
            output = strip_internal_keys(output)
            yield f"event: done\ndata: {json.dumps(output)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Request-ID": request_id},
        )

    handler.__doc__ = f"Stream {endpoint.graph.name} workflow via SSE."
    return handler
