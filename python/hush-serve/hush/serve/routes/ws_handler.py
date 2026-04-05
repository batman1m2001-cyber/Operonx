"""WebSocket bidirectional handler: WS /path/ws.

Uses engine.start() to deliver real-time token events from generator ops,
followed by a final "result" message with the complete output.

Protocol:
  Client -> {"inputs": {...}}
  Server -> {"type": "token", "data": {...}}   (per-token, zero or more)
  Server -> {"type": "result", "data": {...}}  (final complete output)
  Client -> {"type": "close"}
"""

import json
import uuid
from typing import Callable

from fastapi import WebSocket, WebSocketDisconnect

from hush.serve.schema import strip_internal_keys


def create_ws_handler(endpoint) -> Callable:
    """Create a WebSocket handler for bidirectional communication."""
    request_model = endpoint.request_model

    async def handler(websocket: WebSocket):
        await websocket.accept()

        # Extract context from WS handshake headers (same pattern as HTTP middleware)
        request_id = websocket.headers.get("X-Request-ID", str(uuid.uuid4()))
        user_id = websocket.headers.get("X-User-ID")
        session_id = websocket.headers.get("X-Session-ID")

        try:
            while True:
                raw = await websocket.receive_text()
                msg = json.loads(raw)

                if msg.get("type") == "close":
                    break

                inputs = msg.get("inputs", msg)

                try:
                    validated = request_model(**inputs)
                except Exception as e:
                    await websocket.send_json({"type": "error", "data": {"message": str(e)}})
                    continue

                handle = endpoint.engine.start(
                    inputs=validated.model_dump(exclude_none=False),
                    request_id=request_id,
                    user_id=user_id,
                    session_id=session_id,
                )

                async for _op, _ctx, data in handle:
                    await websocket.send_json({"type": "token", "data": data})

                # Build final result from buffered frames (non-consuming)
                output = await handle.result()
                output = strip_internal_keys(output)
                await websocket.send_json({"type": "result", "data": output})

        except WebSocketDisconnect:
            pass

    handler.__doc__ = f"WebSocket for {endpoint.graph.name} workflow."
    return handler
