"""Trace media: swapping a run's blobs for references to a store.

A trace's large values (audio, images, arrays) are kept apart from the
row that mentions them. The row holds a small reference::

    {"$media": "<sha256>", "mime": "audio/wav", "size": 48044,
     "duration_s": 1.5, "sample_rate": 16000, "channels": 1, "store": "local"}

and the bytes live in a :class:`~operonx.core.media_store.MediaStore`,
addressed by their SHA-256, so the same audio is kept once however many
runs carry it.

* :func:`offload_to_store` walks a sanitised value and swaps blobs for
  references; any run store can use it.
* :func:`json_default` does the same while ``orjson`` serialises.

The detector and the store (:func:`detect_media`, :class:`MediaInfo`,
:class:`MediaStore`, :class:`LocalMediaStore`) live in
:mod:`operonx.core.media_store`, since nothing about them is specific to
traces. They are exported here too, so imports from this module keep
working.

Nothing here imports a database driver.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from operonx.core.media_store import (
    _BYTES,
    OCTET,
    LocalMediaStore,
    MediaInfo,
    MediaStore,
    detect_media,
)

__all__ = [
    "OCTET",
    "LocalMediaStore",
    "MediaInfo",
    "MediaStore",
    "detect_media",
    "json_default",
    "offload_to_store",
]


# ── the walk ────────────────────────────────────────────────────────────


def _is_media(v: Any) -> bool:
    return type(v).__module__ == "operonx.core.media" and type(v).__name__ == "Media"


def _is_ndarray(v: Any) -> bool:
    return type(v).__module__ == "numpy" and type(v).__name__ == "ndarray"


def offload_to_store(payload: Any, store: MediaStore, threshold: int = 1024) -> Any:
    """*payload* with its blobs moved into *store* and replaced by refs.

    A :class:`~operonx.core.media.Media` is always stored, whatever its
    size: its producer said it is media. ``bytes``-like values and numpy
    arrays are stored at *threshold* bytes or more and stay inline below
    it. A ``Media`` holding a URL or path keeps it as
    ``{"$media_url": ..., "mime": ...}``. The input is not mutated.
    """
    if isinstance(payload, dict):
        return {k: offload_to_store(v, store, threshold) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [offload_to_store(v, store, threshold) for v in payload]
    if _is_media(payload):
        data, mime = payload.data, payload.mime_type
        if isinstance(data, _BYTES):
            return _put(bytes(data), store, mime)
        return {"$media_url": str(data), "mime": mime}
    if isinstance(payload, _BYTES):
        if len(payload) >= threshold:
            return _put(bytes(payload), store, None)
        return payload
    if _is_ndarray(payload):
        import io

        import numpy as np

        buf = io.BytesIO()
        np.save(buf, payload, allow_pickle=False)
        raw = buf.getvalue()
        return _put(raw, store, None) if len(raw) >= threshold else payload
    return payload


def json_default(store: MediaStore, threshold: int = 1024) -> Any:
    """A ``default=`` hook for ``orjson.dumps`` / ``json.dumps`` that does
    what :meth:`~operonx.telemetry.consumer.Consumer.sanitize` and
    :func:`offload_to_store` do, while the value is being serialised.

    One pass, and the walk itself runs in orjson's C code: the hook is only
    called for what JSON cannot hold. A ``Media`` (passed through with
    ``OPT_PASSTHROUGH_DATACLASS``), large ``bytes`` and large arrays become
    refs; small ``bytes`` their ``repr`` text, small arrays lists, numpy
    scalars numbers; anything else ``{"$unserializable": "<type>"}``.
    """

    def default(v: Any) -> Any:
        if isinstance(v, _BYTES):
            if len(v) >= threshold:
                return _put(bytes(v), store, None)
            return str(bytes(v))
        if _is_media(v):
            if isinstance(v.data, _BYTES):
                return _put(bytes(v.data), store, v.mime_type)
            return {"$media_url": str(v.data), "mime": v.mime_type}
        if _is_ndarray(v):
            if v.nbytes >= threshold:
                return offload_to_store(v, store, threshold)
            return v.tolist()
        if type(v).__module__ == "numpy" and hasattr(v, "item"):
            return v.item()
        return {"$unserializable": type(v).__name__}

    return default


def _put(raw: bytes, store: MediaStore, declared: Optional[str]) -> Dict[str, Any]:
    info = detect_media(raw, declared)
    return info.ref(store.put(raw, info), store.name)
