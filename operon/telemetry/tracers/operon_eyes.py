"""OperonEyesTracer — sends traces to ui-operon-eyes local server.

Uses stdlib urllib.request (no external dependency).
"""

import base64
import json
import logging
import urllib.request
from typing import Any, Dict, List, Optional

from operon.core.tracing.base import Tracer

LOGGER = logging.getLogger("operon.tracing")


class OperonEyesTracer(Tracer):
    """Tracer that sends traces to the ui-operon-eyes local server.

    ui-operon-eyes is a lightweight Rust server that stores traces in SQLite
    and serves a web UI for visualization.

    Example:
        from operon.telemetry import OperonEyesTracer

        tracer = OperonEyesTracer(tags=["dev", "testing"])
        engine = Operon(graph, tracer=tracer)
        result = await engine.run({"x": 5})
        # Open http://localhost:8420 to view traces
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8420,
        tags: Optional[List[str]] = None,
    ):
        super().__init__(tags=tags, stream_trace_limit=None)
        self._host = host
        self._port = port
        self._url = f"http://{host}:{port}/api/ingest"

    def flush(self, trace_data: Dict[str, Any]) -> None:
        """POST trace data to ui-operon-eyes /api/ingest endpoint.

        Args:
            trace_data: Dict matching IngestRequest format
        """
        # Base64-encode any raw bytes in node.media[] so the JSON payload is
        # valid. The ui server can decode these and render previews inline.
        self._encode_media(trace_data)
        body = json.dumps(trace_data, default=str).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    LOGGER.warning(
                        "ui-operon-eyes returned status %d: %s",
                        resp.status,
                        resp.read().decode(),
                    )
        except Exception:
            LOGGER.debug(
                "Could not reach ui-operon-eyes at %s (server may not be running)",
                self._url,
            )

    @staticmethod
    def _encode_media(trace_data: Dict[str, Any]) -> None:
        """Rewrite media blobs in place so raw ``bytes`` become base64 strings."""
        for node in trace_data.get("nodes") or []:
            for ref in node.get("media") or []:
                data = ref.get("data")
                if isinstance(data, (bytes, bytearray)):
                    ref["data"] = base64.b64encode(bytes(data)).decode("ascii")
                    ref["encoding"] = "base64"

    def to_config_dict(self):
        return {"host": self._host, "port": self._port}

    def __repr__(self) -> str:
        return f"<OperonEyesTracer url={self._url}>"
