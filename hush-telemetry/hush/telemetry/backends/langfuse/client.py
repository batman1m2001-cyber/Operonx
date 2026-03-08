"""Langfuse client — direct HTTP API, no SDK dependency.

Uses POST /api/public/ingestion with Basic auth for trace ingestion.
Prompt management methods still use the langfuse SDK (optional import).
"""

import base64
import json
import logging
import urllib.request
from typing import Any, Dict, List, Optional

from hush.telemetry.backends.langfuse.config import LangfuseConfig

LOGGER = logging.getLogger("hush.tracing")


class LangfuseClient:
    """Langfuse client using the public REST API for tracing.

    Tracing (ingest, flush) uses direct HTTP calls — no SDK needed.
    Prompt management (get_prompt, etc.) lazily imports the SDK.

    Example:
        ```python
        from hush.core.registry import get_hub

        client = get_hub().langfuse("default")

        # Ingest trace events (used by LangfuseTracer)
        client.ingest([{"id": "...", "type": "trace-create", "body": {...}}])

        # Prompt management (requires langfuse SDK)
        prompt = client.get_prompt("my-prompt")
        ```
    """

    def __init__(self, config: LangfuseConfig):
        self._config = config
        self._auth = base64.b64encode(f"{config.public_key}:{config.secret_key}".encode()).decode()
        self._ingest_url = f"{config.host.rstrip('/')}/api/public/ingestion"
        # Lazy SDK client for prompt management only
        self._sdk_client = None

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
                "x_langfuse_sdk_name": "python",
                "x_langfuse_sdk_version": "hush",
                "x_langfuse_public_key": self._config.public_key,
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

    # ----------------------------------------------------------------
    # Prompt management — requires langfuse SDK (optional)
    # ----------------------------------------------------------------

    @property
    def _langfuse_sdk(self):
        """Lazy initialization of Langfuse SDK client (for prompt management)."""
        if self._sdk_client is None:
            import os

            from langfuse import Langfuse

            if self._config.no_proxy:
                os.environ["NO_PROXY"] = self._config.no_proxy

            self._sdk_client = Langfuse(
                public_key=self._config.public_key,
                secret_key=self._config.secret_key,
                host=self._config.host,
            )
        return self._sdk_client

    def get_prompt(self, name: str, version: Optional[int] = None, **kwargs):
        """Get a prompt from Langfuse (requires langfuse SDK)."""
        if version:
            return self._langfuse_sdk.get_prompt(name, version=version, **kwargs)
        return self._langfuse_sdk.get_prompt(name, **kwargs)

    def get_prompt_text(self, name: str, version: Optional[int] = None) -> str:
        """Get prompt text content (requires langfuse SDK)."""
        prompt = self.get_prompt(name, version=version)
        return prompt.prompt

    def format_prompt(self, name: str, **variables) -> str:
        """Get and format a prompt with variables (requires langfuse SDK)."""
        prompt_text = self.get_prompt_text(name)
        return prompt_text.format(**variables)

    def __getitem__(self, prompt_name: str) -> str:
        """Get prompt text using bracket notation (requires langfuse SDK)."""
        return self.get_prompt_text(prompt_name)

    def __repr__(self) -> str:
        return f"<LangfuseClient host={self._config.host}>"
