"""Consumer base class for V3 workflow-trace consumers.

A `Consumer` reads a `WorkflowTrace` (attached to `ExecutionHandle.trace`
after a run) and converts it into whatever target-specific form it wants
— a directory tree on disk, a batched HTTP POST to Langfuse, a Loki
stream, a PDF report, a dict for a custom UI, and so on.

Base contract is deliberately small:

* subclasses override :meth:`consume`
* :meth:`sanitize`, :meth:`offload_media`, :meth:`truncate` are
  shared utilities every consumer will want but nothing forces you to
  use them

No I/O, no state, no coupling to any specific backend. Consumers that
DO have state (buffered clients, TTL sweepers, whatever) manage it
themselves in their subclass — the base is pure.

See ``docs/TRACING_V3_DESIGN.md`` §3-5 for the full design.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

from operonx.core.workflow_trace import WorkflowTrace

__all__ = ["Consumer"]


# `bytes | bytearray | memoryview` — value considered "media".
_BYTES_TYPES = (bytes, bytearray, memoryview)


class Consumer(ABC):
    """Base class for any V3 workflow-trace consumer.

    Subclasses override :meth:`consume`. The base offers three shared
    utilities that every real consumer tends to need:

    * :meth:`sanitize` — strip non-JSON-serialisable values so the
      payload can round-trip through ``json.dumps`` / Parquet /
      Langfuse's ingest API.
    * :meth:`offload_media` — replace large binary payloads (audio,
      numpy arrays, model outputs) with content-addressed refs and
      write the raw bytes to a media directory. Dedup is automatic.
    * :meth:`truncate` — cheap string truncation with a hint of the
      dropped length.

    Rules for the base:

    * No I/O, no side effects — every helper is a pure function.
    * No shared state on the instance beyond ``self.config`` (a plain
      dict handed in at ``__init__``).
    * Every helper is opt-in — a minimal consumer can override
      ``consume`` alone and ignore the rest.

    Example — a trivial in-memory consumer::

        class DictConsumer(Consumer):
            def consume(self, trace):
                return {n.op_id: n.op_name for n in trace.nodes}
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config: Dict[str, Any] = dict(config or {})

    @abstractmethod
    def consume(self, trace: WorkflowTrace) -> Any:
        """Convert `trace` to a target-specific artefact.

        Return whatever makes sense for the target — a `Path` (files
        written), URL (posted to a service), dict (in-memory view),
        bytes (a rendered blob). The caller decides what to do with it.

        Should raise on unrecoverable errors so the caller can log +
        continue with the next consumer.
        """

    # ------------------------------------------------------------------
    # Shared utilities
    # ------------------------------------------------------------------

    def sanitize(self, payload: Any) -> Any:
        """Recursively strip non-JSON-serialisable values.

        Anything that isn't `str/int/float/bool/None/dict/list/tuple`
        or a bytes-like object (which `offload_media` handles) gets
        replaced with ``{"$unserializable": "<type_name>"}``. Nested
        dicts/lists are walked in place.

        Numpy arrays go through unchanged — `offload_media` will
        recognise + offload them. Callers that don't want numpy inline
        should run `offload_media` FIRST.
        """
        if isinstance(payload, dict):
            return {k: self.sanitize(v) for k, v in payload.items()}
        if isinstance(payload, list):
            return [self.sanitize(v) for v in payload]
        if isinstance(payload, tuple):
            return [self.sanitize(v) for v in payload]
        if isinstance(payload, (str, int, float, bool)) or payload is None:
            return payload
        if isinstance(payload, _BYTES_TYPES):
            # Leave for offload_media OR downstream serializer to handle
            # explicitly — sanitize deliberately doesn't strip these.
            return payload
        # numpy arrays / Media objects / anything with array-like buffer
        # protocol — leave for offload_media.
        if _is_ndarray(payload) or _is_media(payload):
            return payload
        return {"$unserializable": type(payload).__name__}

    def offload_media(
        self,
        payload: Any,
        media_dir: Path,
        threshold: int = 1024,
    ) -> Any:
        """Recursively offload large binary values to `media_dir`.

        Any value that's `bytes` / `bytearray` / `memoryview` /
        `numpy.ndarray` / `operonx.core.media.Media` above `threshold`
        bytes gets:

        1. serialised to bytes,
        2. hashed with SHA-256,
        3. written to ``media_dir/<sha256>.<ext>`` (natural dedup — same
           bytes → same file, atomic `open("xb")` with `EEXIST`
           tolerated),
        4. replaced in the payload with
           ``{"$media_ref": "media/<sha256>.<ext>", "size": <bytes>}``.

        Payloads smaller than `threshold` stay inline so the trace
        remains greppable for small state dicts. Returns a new payload
        (input is not mutated).
        """
        media_dir.mkdir(parents=True, exist_ok=True)
        return self._offload_walk(payload, media_dir, threshold)

    def truncate(self, s: str, limit: int = 500) -> str:
        """Cap `s` at `limit` chars, appending a length hint if dropped."""
        if len(s) <= limit:
            return s
        dropped = len(s) - limit
        return f"{s[:limit]}…(+{dropped})"

    # ------------------------------------------------------------------
    # Internals — media offload walk
    # ------------------------------------------------------------------

    def _offload_walk(self, v: Any, media_dir: Path, threshold: int) -> Any:
        if isinstance(v, dict):
            return {k: self._offload_walk(x, media_dir, threshold) for k, x in v.items()}
        if isinstance(v, list):
            return [self._offload_walk(x, media_dir, threshold) for x in v]
        if isinstance(v, tuple):
            return [self._offload_walk(x, media_dir, threshold) for x in v]
        raw, ext = _serialise_media(v)
        if raw is None or len(raw) < threshold:
            return v
        sha = hashlib.sha256(raw).hexdigest()
        path = media_dir / f"{sha}.{ext}"
        if not path.exists():
            # Atomic-ish write: create-exclusive so concurrent writers
            # of identical bytes don't overwrite each other.
            try:
                with path.open("xb") as f:
                    f.write(raw)
            except FileExistsError:
                pass  # someone else won the race — same content, fine
        return {"$media_ref": f"media/{path.name}", "size": len(raw)}


# ---------------------------------------------------------------------------
# Media type detection / serialisation
# ---------------------------------------------------------------------------


def _is_ndarray(v: Any) -> bool:
    """True for numpy arrays without importing numpy at module load."""
    return type(v).__module__ == "numpy" and type(v).__name__ == "ndarray"


def _is_media(v: Any) -> bool:
    """True for `operonx.core.media.Media`."""
    return type(v).__module__ == "operonx.core.media" and type(v).__name__ == "Media"


def _serialise_media(v: Any) -> tuple:
    """Return `(raw_bytes, extension)` for offloadable values, else `(None, "")`.

    Extensions are heuristic — enough to open the file in the right
    tool without guessing:

    * numpy → ``.npy`` (uses `numpy.save` for full roundtrip)
    * ``Media(data=bytes, mime=<mime>)`` → extension inferred from mime
    * raw ``bytes / bytearray / memoryview`` → ``.bin``
    """
    if isinstance(v, _BYTES_TYPES):
        return bytes(v), "bin"
    if _is_media(v):
        # v.data is bytes; v.mime_type is like "audio/wav" or None
        data = getattr(v, "data", None)
        mime = getattr(v, "mime_type", None) or ""
        ext = mime.split("/")[-1] if "/" in mime else "bin"
        if isinstance(data, _BYTES_TYPES):
            return bytes(data), ext
        return None, ""
    if _is_ndarray(v):
        import io

        import numpy as np

        buf = io.BytesIO()
        np.save(buf, v, allow_pickle=False)
        return buf.getvalue(), "npy"
    return None, ""
