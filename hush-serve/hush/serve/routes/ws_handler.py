"""WebSocket bidirectional handler: WS /path/ws.

NOTE: Streaming consumer logic (STREAM_SERVICE) has been removed.
This handler currently runs the workflow and returns the final result.
A callback-based streaming mechanism will be added in a future phase.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Callable

from fastapi import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from hush.serve.endpoint import Endpoint


def create_ws_handler(endpoint: "Endpoint") -> Callable:
    """Create a WebSocket handler for bidirectional communication.

    Protocol:
      Client -> {"inputs": {...}}
      Server -> {"type": "result", "data": {full output}}
      Client -> {"type": "close"}
    """
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

                result = await endpoint.engine.run(
                    inputs=validated.model_dump(exclude_none=False),
                    request_id=request_id,
                    tracer=endpoint.tracer,
                )

                output = {k: v for k, v in result.items() if not k.startswith("$")}
                await websocket.send_json({"type": "result", "data": output})

        except WebSocketDisconnect:
            pass

    handler.__doc__ = f"WebSocket for {endpoint.graph.name} workflow."
    return handler
