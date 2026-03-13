"""WebSocket bidirectional handler: WS /path/ws.

Uses engine.stream() to deliver real-time token events from generator ops,
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


def create_ws_handler(endpoint) -> Callable:
    """Create a WebSocket handler for bidirectional communication."""
    request_model = endpoint.request_model

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

                async for event in endpoint.engine.stream(
                    inputs=validated.model_dump(exclude_none=False),
                    request_id=request_id,
                    tracer=endpoint.tracer,
                ):
                    if event["type"] == "token":
                        await websocket.send_json({"type": "token", "data": event["data"]})
                    elif event["type"] == "done":
                        output = {k: v for k, v in event["data"].items() if not k.startswith("$")}
                        await websocket.send_json({"type": "result", "data": output})

        except WebSocketDisconnect:
            pass

    handler.__doc__ = f"WebSocket for {endpoint.graph.name} workflow."
    return handler
