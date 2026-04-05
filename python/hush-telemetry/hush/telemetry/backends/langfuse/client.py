"""Langfuse client — direct HTTP API, no SDK dependency.

Uses POST /api/public/ingestion with Basic auth for trace ingestion.
For prompt management, see LangfusePromptManager (requires langfuse SDK).
"""

import base64
import json
import logging
import urllib.request
from typing import Any, Dict, List

from hush.telemetry.backends.langfuse.config import LangfuseConfig

LOGGER = logging.getLogger("hush.tracing")


class LangfuseClient:
    """Langfuse client using the public REST API for tracing.

    Pure HTTP — no SDK dependency. Used by LangfuseTracer for trace ingestion.

    Example:
        ```python
        from hush.core.registry import get_hub

        client = get_hub().get("langfuse:default")
        client.ingest([{"id": "...", "type": "trace-create", "body": {...}}])
        ```
    """

    def __init__(self, config: LangfuseConfig):
        self._config = config
        self._auth = base64.b64encode(f"{config.public_key}:{config.secret_key}".encode()).decode()
        self._ingest_url = f"{config.host.rstrip('/')}/api/public/ingestion"

    @property
    def config(self) -> LangfuseConfig:
        return self._config

    def ingest(self, batch: List[Dict[str, Any]], timeout: int = 30) -> Dict[str, Any]:
        """Send a batch of events to Langfuse ingestion API.

        Args:
            batch: List of ingestion events (trace-create, span-create, etc.)
            timeout: Request timeout in seconds

        Returns:
            Response dict with 'successes' and 'errors' lists
        """
        metadata = {
            "batch_size": len(batch),
            "sdk_integration": "default",
            "sdk_name": "python",
            "sdk_version": "hush",
            "public_key": self._config.public_key,
        }
        body = json.dumps({"batch": batch, "metadata": metadata}, default=str).encode("utf-8")
        req = urllib.request.Request(
            self._ingest_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {self._auth}",
                "X-Langfuse-Sdk-Name": "python",
                "X-Langfuse-Sdk-Version": "hush",
                "X-Langfuse-Public-Key": self._config.public_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def trace_url(self, trace_id: str) -> str:
        """Build the Langfuse UI URL for a trace."""
        return f"{self._config.host.rstrip('/')}/trace/{trace_id}"

    def auth_check(self) -> bool:
        """Check authentication by hitting the health endpoint.

        Returns:
            True if authentication is successful
        """
        url = f"{self._config.host.rstrip('/')}/api/public/health"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Basic {self._auth}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False

    def __repr__(self) -> str:
        return f"<LangfuseClient host={self._config.host}>"
