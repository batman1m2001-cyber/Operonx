"""Sync request-response handler: POST /path -> JSON result."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Callable

from fastapi import Request

if TYPE_CHECKING:
    from hush.serve.endpoint import Endpoint


def create_sync_handler(endpoint: "Endpoint") -> Callable:
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
            tracer=endpoint.tracer,
        )

        return {k: v for k, v in result.items() if not k.startswith("$")}

    handler.__doc__ = f"Execute {endpoint.graph.name} workflow."
    return handler
