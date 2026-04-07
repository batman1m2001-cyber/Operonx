"""Sync request-response handler: POST /path -> JSON result."""

import uuid
from typing import Callable

from fastapi import Request

from hush.serve.schema import strip_internal_keys


def create_sync_handler(endpoint) -> Callable:
    """Create a FastAPI route handler for sync execution."""
    request_model = endpoint.request_model

    async def handler(body: request_model, request: Request):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        user_id = request.headers.get("X-User-ID")
        session_id = request.headers.get("X-Session-ID")

        result = await endpoint.engine.run(
            inputs=body.model_dump(exclude_none=False),
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
        )

        return strip_internal_keys(result)

    handler.__doc__ = f"Execute {endpoint.graph.name} workflow."
    return handler
