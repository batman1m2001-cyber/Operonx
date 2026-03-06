"""SSE streaming handler: POST /path/stream -> text/event-stream.

NOTE: Streaming consumer logic (STREAM_SERVICE) has been removed.
This handler currently runs the workflow and returns the final result as
a single SSE "done" event. A callback-based streaming mechanism will be
added in a future phase.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Callable

from fastapi import Request
from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from hush.serve.endpoint import Endpoint


def create_stream_handler(endpoint: "Endpoint") -> Callable:
    """Create a FastAPI route handler for SSE streaming."""
    request_model = endpoint.request_model

    async def handler(body: request_model, request: Request):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        user_id = request.headers.get("X-User-ID")
        session_id = request.headers.get("X-Session-ID")

        async def event_generator():
            result = await endpoint.engine.run(
                inputs=body.model_dump(exclude_none=False),
                request_id=request_id,
                user_id=user_id,
                session_id=session_id,
                tracer=endpoint.tracer,
            )
            output = {k: v for k, v in result.items() if not k.startswith("$")}
            yield f"event: done\ndata: {json.dumps(output)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Request-ID": request_id},
        )

    handler.__doc__ = f"Stream {endpoint.graph.name} workflow via SSE."
    return handler
