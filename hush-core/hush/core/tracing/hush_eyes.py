"""HushEyesTracer — sends traces to ui-hush-eyes local server.

Uses stdlib urllib.request (no external dependency).
"""

import json
import logging
import urllib.request
from typing import Any, Dict, List, Optional

from hush.core.tracing.base import Tracer

LOGGER = logging.getLogger("hush.tracing")


class HushEyesTracer(Tracer):
    """Tracer that sends traces to the ui-hush-eyes local server.

    ui-hush-eyes is a lightweight Rust server that stores traces in SQLite
    and serves a web UI for visualization.

    Example:
        from hush.core.tracing import HushEyesTracer

        tracer = HushEyesTracer(tags=["dev", "testing"])
        engine = Hush(graph)
        result = await engine.run({"x": 5}, tracer=tracer)
        # Open http://localhost:8420 to view traces
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8420,
        tags: Optional[List[str]] = None,
    ):
        super().__init__(tags=tags, stream_trace_limit=None)
        self._url = f"http://{host}:{port}/api/ingest"

    def flush(self, trace_data: Dict[str, Any]) -> None:
        """POST trace data to ui-hush-eyes /api/ingest endpoint.

        Args:
            trace_data: Dict matching IngestRequest format
        """
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
                        "ui-hush-eyes returned status %d: %s",
                        resp.status,
                        resp.read().decode(),
                    )
        except Exception:
            LOGGER.debug(
                "Could not reach ui-hush-eyes at %s (server may not be running)",
                self._url,
            )

    def __repr__(self) -> str:
        return f"<HushEyesTracer url={self._url}>"
