"""Media wrapper for trace extraction.

Producer ops wrap media values once at the source::

    @op
    def tts(text: str):
        return {"audio": Media(synthesize(text), mime_type="audio/mp3")}

Downstream consumers read the raw value — ``BaseOp._get_inputs`` auto-unwraps
``Media`` instances so schemas stay plain (``Param(type=bytes)``) and no
``Media`` import is needed in consumer code.

The collector walks state directly (not via input binding), so it sees the
wrapper intact and extracts blobs into a parallel ``node.media`` list before
flushing to a tracer backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

__all__ = ["Media", "MediaRef"]


@dataclass(frozen=True)
class Media:
    """Marker wrapping a value as extractable media for tracing.

    Two fields only. ``filename`` was considered and rejected: it is cosmetic,
    usually absent (TTS / image-gen have no natural name), and can be added
    backwards-compatibly if a real need appears. Producers that want to
    preserve a user-uploaded name pass it as a sibling output key alongside
    the media value.
    """

    data: Union[bytes, str]
    """Raw bytes, a ``data:`` URL, an http(s) URL, or a file path."""

    mime_type: str
    """MIME type like ``image/png``, ``audio/mp3``, ``video/webm``."""


@dataclass
class MediaRef:
    """Collector-emitted reference to an extracted media blob.

    The collector replaces each ``Media`` instance in a node's trace I/O with
    a ``<media:N>`` placeholder string and appends a ``MediaRef`` to the node's
    ``media`` list. Tracers walk the list, upload or drop each blob per their
    backend's capabilities, then substitute the placeholder in the I/O dict at
    ``field_path``.

    ``field_path`` format
        Dotted JSONPath-style, rooted at ``"inputs"`` or ``"outputs"``:

          - ``"inputs.audio"``                       — top-level key
          - ``"inputs.payload.image"``               — nested dict
          - ``"inputs.messages[0].content[1].image_url.url"`` — list indices
          - ``"outputs.results[2].thumbnail"``       — mixed

        ``size_bytes`` is a convenience mirror of ``len(data)`` when data is
        bytes, included so tracers can report size without materializing the
        blob. For ``str`` data (URL / path) it is the string length in bytes.
    """

    field_path: str
    data: Union[bytes, str]
    mime_type: str
    size_bytes: int

    @classmethod
    def from_media(cls, media: Media, field_path: str) -> "MediaRef":
        size = len(media.data) if isinstance(media.data, (bytes, bytearray)) else len(media.data)
        return cls(
            field_path=field_path,
            data=media.data,
            mime_type=media.mime_type,
            size_bytes=size,
        )


def extract_media(io_dict: dict, root: str) -> tuple[dict, list[MediaRef]]:
    """Recursively strip ``Media`` instances from an I/O dict.

    Args:
        io_dict: The trace I/O dict (inputs or outputs) after
            ``BaseOp.normalize_trace_io`` has run.
        root: Either ``"inputs"`` or ``"outputs"`` — the field_path prefix.

    Returns:
        ``(stripped_dict, media_refs)`` — a deep-copied dict with ``Media``
        instances replaced by ``"<media:N>"`` placeholders, and a list of
        ``MediaRef`` entries carrying the blobs plus their field paths.
    """
    refs: list[MediaRef] = []

    def _walk(value, path: str):
        if isinstance(value, Media):
            ref = MediaRef.from_media(value, path)
            idx = len(refs)
            refs.append(ref)
            return f"<media:{idx}>"
        if isinstance(value, dict):
            return {k: _walk(v, f"{path}.{k}") for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(v, f"{path}[{i}]") for i, v in enumerate(value)]
        if isinstance(value, tuple):
            return tuple(_walk(v, f"{path}[{i}]") for i, v in enumerate(value))
        return value

    stripped = {k: _walk(v, f"{root}.{k}") for k, v in io_dict.items()}
    return stripped, refs


def substitute_placeholder(io_dict: dict, field_path: str, replacement) -> bool:
    """Walk a trace I/O dict along ``field_path`` and swap the leaf value.

    Used by tracers after uploading a ``MediaRef`` to put the upload reference
    (e.g. a Langfuse media token) back in place of the ``<media:N>``
    placeholder.

    Returns ``True`` on successful substitution, ``False`` if the path no
    longer exists (tracer should log and skip).
    """
    # field_path is like "inputs.messages[0].content[1].image_url.url"
    # Split the first segment off — it must be "inputs" or "outputs".
    segments = _split_path(field_path)
    if not segments:
        return False
    # First segment is the root key name already inside io_dict ("inputs" or
    # "outputs"), which is wrong — io_dict IS already inputs or outputs, so
    # skip the first segment.
    segments = segments[1:]
    if not segments:
        return False

    cursor = io_dict
    for seg in segments[:-1]:
        cursor = _step(cursor, seg)
        if cursor is None:
            return False
    last = segments[-1]
    return _set(cursor, last, replacement)


def _split_path(path: str) -> list:
    """Split ``inputs.messages[0].content`` into ``['inputs', 'messages', 0, 'content']``."""
    out: list = []
    buf = ""
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == ".":
            if buf:
                out.append(buf)
                buf = ""
            i += 1
        elif ch == "[":
            if buf:
                out.append(buf)
                buf = ""
            end = path.find("]", i)
            if end < 0:
                return []
            out.append(int(path[i + 1 : end]))
            i = end + 1
        else:
            buf += ch
            i += 1
    if buf:
        out.append(buf)
    return out


def _step(cursor, seg):
    if isinstance(seg, int):
        if isinstance(cursor, (list, tuple)) and 0 <= seg < len(cursor):
            return cursor[seg]
        return None
    if isinstance(cursor, dict):
        return cursor.get(seg)
    return None


def _set(cursor, seg, value) -> bool:
    if isinstance(seg, int):
        if isinstance(cursor, list) and 0 <= seg < len(cursor):
            cursor[seg] = value
            return True
        return False
    if isinstance(cursor, dict):
        if seg not in cursor:
            return False
        cursor[seg] = value
        return True
    return False
