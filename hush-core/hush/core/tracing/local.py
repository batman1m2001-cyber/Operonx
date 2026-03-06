"""LocalTracer — writes trace data to local JSON files.

Zero dependencies, zero setup. One JSON file per request.
Default path: ~/.hush/traces/ (configurable via path= or HUSH_TRACES_DIR env var).
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from hush.core.tracing.base import Tracer

LOGGER = logging.getLogger("hush.tracing")


class LocalTracer(Tracer):
    """Tracer that writes trace data to local JSON files.

    Each engine.run() produces one file: {path}/{request_id}.json
    containing the full TracePayload as pretty-printed JSON.

    Example:
        from hush.core.tracing import LocalTracer

        engine = Hush(graph)
        result = await engine.run(inputs, tracer=LocalTracer())
        # Trace written to ~/.hush/traces/{request_id}.json

        # Custom path
        result = await engine.run(inputs, tracer=LocalTracer(path="./my-traces"))
    """

    def __init__(
        self,
        path: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ):
        super().__init__(tags=tags, stream_trace_limit=None)
        self._path = Path(
            path or os.environ.get("HUSH_TRACES_DIR", "~/.hush/traces")
        ).expanduser()

    def flush(self, trace_data: Dict[str, Any]) -> None:
        """Write trace data to a JSON file.

        Args:
            trace_data: Dict matching TracePayload format.
        """
        self._path.mkdir(parents=True, exist_ok=True)
        request_id = trace_data.get("request_id", "unknown")
        filepath = self._path / f"{request_id}.json"
        filepath.write_text(json.dumps(trace_data, indent=2, default=str))
        LOGGER.info("Trace written to %s", filepath)

    def __repr__(self) -> str:
        return f"<LocalTracer path={self._path}>"
