"""Langfuse client — direct HTTP API, no SDK dependency.

Uses POST /api/public/ingestion with Basic auth for trace ingestion.
For prompt management, see LangfusePromptManager (requires langfuse SDK).
"""

import base64
import hashlib
import json
import logging
import urllib.request
from typing import Any, Dict, List, Optional

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

    def upload_media(
        self,
        *,
        trace_id: str,
        field: str,
        content_type: str,
        content: bytes,
        observation_id: Optional[str] = None,
        timeout: int = 30,
    ) -> Optional[str]:
        """Upload a media blob and return its Langfuse media reference token.

        Two-step flow per Langfuse docs:
          1. POST /api/public/media with metadata → get mediaId + presigned URL.
          2. PUT the raw bytes to the presigned URL with the required headers.

        Args:
            trace_id: Trace this media belongs to.
            field: Where the media lives — ``"input"``, ``"output"``, or
                ``"metadata"``.
            content_type: MIME type, e.g. ``"image/png"``.
            content: Raw bytes to upload.
            observation_id: Optional observation (span / generation) ID.
            timeout: HTTP timeout per request.

        Returns:
            The reference token string
            ``@@@langfuseMedia:type=<mime>|id=<mediaId>|source=bytes@@@``,
            or ``None`` if the upload failed.
        """
        content_length = len(content)
        sha256 = base64.b64encode(hashlib.sha256(content).digest()).decode()

        body_dict: Dict[str, Any] = {
            "traceId": trace_id,
            "field": field,
            "contentType": content_type,
            "contentLength": content_length,
            "sha256Hash": sha256,
        }
        if observation_id:
            body_dict["observationId"] = observation_id

        url = f"{self._config.host.rstrip('/')}/api/public/media"
        req = urllib.request.Request(
            url,
            data=json.dumps(body_dict).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {self._auth}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                init = json.loads(resp.read())
        except Exception as e:
            LOGGER.warning("Langfuse media init failed: %s", e)
            return None

        upload_url = init.get("uploadUrl")
        media_id = init.get("mediaId")
        if not media_id:
            LOGGER.warning("Langfuse media init returned no mediaId: %r", init)
            return None

        # If the server returned a null uploadUrl the blob is already stored
        # (dedup by sha256). Skip the PUT and return the token.
        if upload_url:
            upload_method = init.get("uploadHttpMethod") or "PUT"
            # Langfuse's own SDK ignores uploadHttpHeaders from the init
            # response and constructs headers client-side. The presigned S3
            # URL is signed for exactly these headers, so we must send them
            # verbatim:
            #   Content-Type             — matches declared contentType
            #   x-amz-checksum-sha256    — S3 integrity check
            #   x-ms-blob-type           — Azure Blob (ignored by S3)
            upload_headers = {
                "Content-Type": content_type,
                "x-amz-checksum-sha256": sha256,
                "x-ms-blob-type": "BlockBlob",
            }

            put_req = urllib.request.Request(
                upload_url,
                data=content,
                headers=upload_headers,
                method=upload_method,
            )
            try:
                with urllib.request.urlopen(put_req, timeout=timeout) as put_resp:
                    if put_resp.status >= 300:
                        LOGGER.warning("Langfuse media PUT returned %d", put_resp.status)
                        return None
                    # Some providers require an explicit PATCH to confirm the
                    # upload (see below).
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode(errors="replace")[:500]
                except Exception:
                    pass
                LOGGER.warning(
                    "Langfuse media PUT failed: %s (headers=%s body=%r)",
                    e,
                    list(upload_headers.keys()),
                    body,
                )
                return None
            except Exception as e:
                LOGGER.warning("Langfuse media PUT failed: %s", e)
                return None

            # Langfuse expects a PATCH to /api/public/media/{mediaId} to mark
            # the upload complete with the status code + timing. Without this
            # the blob is orphaned and the reference token won't resolve.
            self._confirm_media_upload(media_id, 200, timeout)

        return f"@@@langfuseMedia:type={content_type}|id={media_id}|source=bytes@@@"

    def _confirm_media_upload(self, media_id: str, upload_http_status: int, timeout: int) -> None:
        """PATCH /api/public/media/{id} to confirm a successful upload."""
        from datetime import datetime, timezone

        url = f"{self._config.host.rstrip('/')}/api/public/media/{media_id}"
        body = {
            "uploadedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "uploadHttpStatus": upload_http_status,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {self._auth}",
            },
            method="PATCH",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status >= 300:
                    LOGGER.warning("Langfuse media PATCH confirm returned %d", resp.status)
        except Exception as e:
            LOGGER.warning("Langfuse media PATCH confirm failed: %s", e)

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
