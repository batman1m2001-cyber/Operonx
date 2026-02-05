"""Media attachment utilities for multimodal tracing.

This module provides utilities for attaching media (images, PDFs, audio, etc.)
to workflow traces. Media can be attached to input, output, or metadata of a trace.

Usage in CodeNode or custom nodes:

    ```python
    def my_code_fn(pdf_bytes: bytes) -> Dict:
        # Process the PDF...
        result = process_pdf(pdf_bytes)

        return {
            "result": result,
            "__media__": {
                "input_document": {
                    "bytes": pdf_bytes,
                    "content_type": "application/pdf",
                    "attach_to": "input"
                },
                "output_chart": {
                    "bytes": chart_png_bytes,
                    "content_type": "image/png",
                    "attach_to": "output"
                }
            }
        }
    ```
"""
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional
import base64


# Reserved key for media attachments in node outputs
MEDIA_KEY = "__media__"

# Maximum size for inline base64 encoding (1MB)
# Larger files should use file path reference
MAX_INLINE_SIZE = 1 * 1024 * 1024


@dataclass
class MediaAttachment:
    """Represents a media attachment for tracing.

    Attributes:
        content_type: MIME type (e.g., "image/png", "application/pdf", "audio/wav")
        attach_to: Where to attach in trace ("input", "output", or "metadata")
        base64: Base64-encoded content for small files
        path: File path for large files (resolved during flush)
    """
    content_type: str
    attach_to: Literal["input", "output", "metadata"] = "metadata"
    base64: Optional[str] = None
    path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to serializable dictionary."""
        result = {
            "content_type": self.content_type,
            "attach_to": self.attach_to,
        }
        if self.base64:
            result["base64"] = self.base64
        if self.path:
            result["path"] = self.path
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MediaAttachment":
        """Create from dictionary."""
        return cls(
            content_type=data["content_type"],
            attach_to=data.get("attach_to", "metadata"),
            base64=data.get("base64"),
            path=data.get("path"),
        )

    @classmethod
    def from_bytes(
        cls,
        content: bytes,
        content_type: str,
        attach_to: Literal["input", "output", "metadata"] = "metadata"
    ) -> "MediaAttachment":
        """Create MediaAttachment from bytes.

        For small files (<1MB), stores as base64.
        For larger files, you should save to a temp file and use from_path().

        Args:
            content: The binary content
            content_type: MIME type of the content
            attach_to: Where to attach in trace

        Returns:
            MediaAttachment instance
        """
        return cls(
            content_type=content_type,
            attach_to=attach_to,
            base64=base64.b64encode(content).decode("utf-8"),
        )

    @classmethod
    def from_path(
        cls,
        path: str,
        content_type: str,
        attach_to: Literal["input", "output", "metadata"] = "metadata"
    ) -> "MediaAttachment":
        """Create MediaAttachment from file path.

        The file will be read during flush in the subprocess.

        Args:
            path: Path to the file
            content_type: MIME type of the content
            attach_to: Where to attach in trace

        Returns:
            MediaAttachment instance
        """
        return cls(
            content_type=content_type,
            attach_to=attach_to,
            path=path,
        )


def serialize_media_attachments(media_dict: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Serialize media attachments for subprocess transfer.

    Converts raw bytes to base64 or file paths, preparing for serialization.

    Args:
        media_dict: Dictionary with media attachment definitions.
            Each value can be:
            - MediaAttachment object
            - Dict with "bytes", "content_type", and optionally "attach_to"
            - Dict with "path", "content_type", and optionally "attach_to"

    Returns:
        Dictionary of serialized media attachments
    """
    serialized = {}

    for key, value in media_dict.items():
        if isinstance(value, MediaAttachment):
            serialized[key] = value.to_dict()
        elif isinstance(value, dict):
            # Handle raw dict format from CodeNode
            if "bytes" in value:
                content_bytes = value["bytes"]
                content_type = value.get("content_type", "application/octet-stream")
                attach_to = value.get("attach_to", "metadata")

                # Encode bytes to base64
                if isinstance(content_bytes, bytes):
                    serialized[key] = {
                        "content_type": content_type,
                        "attach_to": attach_to,
                        "base64": base64.b64encode(content_bytes).decode("utf-8"),
                    }
                else:
                    # Already base64 string
                    serialized[key] = {
                        "content_type": content_type,
                        "attach_to": attach_to,
                        "base64": content_bytes,
                    }
            elif "path" in value:
                serialized[key] = {
                    "content_type": value.get("content_type", "application/octet-stream"),
                    "attach_to": value.get("attach_to", "metadata"),
                    "path": value["path"],
                }
            elif "base64" in value:
                # Already serialized format
                serialized[key] = value

    return serialized