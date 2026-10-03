"""Trace media: what a blob is, and where it is kept.

A trace's large values (audio, images, arrays) are kept apart from the
row that mentions them. The row holds a small reference::

    {"$media": "<sha256>", "mime": "audio/wav", "size": 48044,
     "duration_s": 1.5, "sample_rate": 16000, "channels": 1, "store": "local"}

and the bytes live in a :class:`MediaStore`, addressed by their SHA-256,
so the same audio is kept once however many runs carry it.

* :func:`detect_media` names a blob's type from its first bytes (WAV,
  MP3, OGG/Opus, FLAC, WebM, PNG, JPEG, GIF, WebP, PDF, ``.npy``) and
  reads a duration where the header carries one. Raw PCM has no header:
  it takes the rate a :class:`~operonx.core.media.Media` declares in its
  mime parameters (``audio/L16;rate=16000;channels=1``).
* :class:`LocalMediaStore` keeps blobs in a directory. Another backend
  (S3, MinIO) implements the same five methods.
* :func:`offload_to_store` walks a sanitised value and swaps blobs for
  references; any run store can use it.

Nothing here imports a database driver.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import struct
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

__all__ = [
    "OCTET",
    "LocalMediaStore",
    "MediaInfo",
    "MediaStore",
    "detect_media",
    "offload_to_store",
]

#: The type of bytes nothing recognises.
OCTET = "application/octet-stream"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_BYTES = (bytes, bytearray, memoryview)


@dataclass
class MediaInfo:
    """What a blob is. ``duration_s``, ``sample_rate`` and ``channels`` are
    set only when the bytes (or a declared PCM type) say so."""

    mime: str = OCTET
    ext: str = "bin"
    size: int = 0
    duration_s: Optional[float] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None

    def ref(self, sha: str, store: str) -> Dict[str, Any]:
        """The reference a trace row keeps in place of the bytes."""
        out: Dict[str, Any] = {"$media": sha, "mime": self.mime, "size": self.size}
        if self.duration_s is not None:
            out["duration_s"] = round(self.duration_s, 6)
        if self.sample_rate is not None:
            out["sample_rate"] = self.sample_rate
        if self.channels is not None:
            out["channels"] = self.channels
        out["store"] = store
        return out


# ── detection ───────────────────────────────────────────────────────────


def detect_media(data: Any, declared: Optional[str] = None) -> MediaInfo:
    """A blob's type from its magic bytes, else from *declared*.

    The bytes win whenever they identify a format: a producer that labels
    WAV as ``audio/mp3`` gets ``audio/wav``. When they do not, a declared
    type is kept, and a declared raw-PCM type (``audio/L16``, ``audio/L8``,
    ``audio/L24``, ``audio/pcm``, ``audio/raw``) with a ``rate=`` parameter
    gets a duration from its size, ``channels=`` (default 1) and the bit
    depth (from the ``L`` number, else ``bits=``, default 16). Never raises.
    """
    raw = bytes(data) if isinstance(data, _BYTES) else b""
    try:
        info = _magic(raw)
    except Exception:  # noqa: BLE001 — a malformed header is just unknown
        info = None
    if info is None:
        info = _declared(raw, declared) if declared else MediaInfo()
    info.size = len(raw)
    return info


def _magic(b: bytes) -> Optional[MediaInfo]:
    if len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WAVE":
        return _wav(b)
    if len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return MediaInfo("image/webp", "webp")
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return MediaInfo("image/png", "png")
    if b[:3] == b"\xff\xd8\xff":
        return MediaInfo("image/jpeg", "jpg")
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return MediaInfo("image/gif", "gif")
    if b[:5] == b"%PDF-":
        return MediaInfo("application/pdf", "pdf")
    if b[:4] == b"fLaC":
        return _flac(b)
    if b[:4] == b"OggS":
        return _ogg(b)
    if b[:4] == b"\x1a\x45\xdf\xa3":
        return _ebml(b)
    if b[:6] == b"\x93NUMPY":
        return MediaInfo("application/x-npy", "npy")
    if b[:3] == b"ID3" or _mp3_frame(b[:4]):
        return MediaInfo("audio/mpeg", "mp3")
    return None


def _wav(b: bytes) -> MediaInfo:
    info = MediaInfo("audio/wav", "wav")
    pos, byte_rate = 12, 0
    while pos + 8 <= len(b):
        cid, size = b[pos : pos + 4], struct.unpack("<I", b[pos + 4 : pos + 8])[0]
        body = pos + 8
        if cid == b"fmt " and body + 16 <= len(b):
            _fmt, channels, rate, byte_rate = struct.unpack("<HHII", b[body : body + 12])
            info.channels, info.sample_rate = channels or None, rate or None
        elif cid == b"data":
            # a streamed WAV leaves the size at 0 or 0xFFFFFFFF: take what is there
            avail = len(b) - body
            data_size = size if 0 < size <= avail else avail
            if byte_rate:
                info.duration_s = data_size / byte_rate
            break
        pos = body + size + (size & 1)
    return info


def _flac(b: bytes) -> MediaInfo:
    info = MediaInfo("audio/flac", "flac")
    if len(b) >= 26 and (b[4] & 0x7F) == 0:  # STREAMINFO comes first
        v = int.from_bytes(b[18:26], "big")
        rate = v >> 44
        info.sample_rate = rate or None
        info.channels = ((v >> 41) & 0x7) + 1
        total = v & ((1 << 36) - 1)
        if rate and total:
            info.duration_s = total / rate
    return info


def _ogg(b: bytes) -> MediaInfo:
    head = b.find(b"OpusHead", 0, 512)
    if head >= 0 and head + 16 <= len(b):
        info = MediaInfo("audio/opus", "opus")
        info.channels = b[head + 9] or None
        pre_skip, rate = struct.unpack("<HI", b[head + 10 : head + 16])
        info.sample_rate = rate or 48000
        granule = _last_granule(b)
        if granule is not None and granule > pre_skip:
            info.duration_s = (granule - pre_skip) / 48000.0  # Opus always counts at 48 kHz
        return info
    info = MediaInfo("audio/ogg", "ogg")
    vorbis = b.find(b"\x01vorbis", 0, 512)
    if vorbis >= 0 and vorbis + 16 <= len(b):
        info.channels = b[vorbis + 11] or None
        info.sample_rate = struct.unpack("<I", b[vorbis + 12 : vorbis + 16])[0] or None
        granule = _last_granule(b)
        if granule and info.sample_rate:
            info.duration_s = granule / info.sample_rate
    return info


def _last_granule(b: bytes) -> Optional[int]:
    last = b.rfind(b"OggS")
    if last < 0 or last + 14 > len(b):
        return None
    granule = struct.unpack("<q", b[last + 6 : last + 14])[0]
    return granule if granule > 0 else None


def _ebml(b: bytes) -> MediaInfo:
    at = b.find(b"\x42\x82", 4, 64)
    if at >= 0 and at + 3 <= len(b) and b[at + 2] & 0x80:
        n = b[at + 2] & 0x7F
        doctype = b[at + 3 : at + 3 + n]
        if doctype == b"matroska":
            return MediaInfo("video/x-matroska", "mkv")
    return MediaInfo("video/webm", "webm")


def _mp3_frame(h: bytes) -> bool:
    """A valid MPEG audio frame header: sync, a real version, layer,
    bitrate and sample rate — not just two 0xFF-ish bytes."""
    if len(h) < 4 or h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
        return False
    version, layer = (h[1] >> 3) & 0x3, (h[1] >> 1) & 0x3
    bitrate, rate = h[2] >> 4, (h[2] >> 2) & 0x3
    return version != 1 and layer != 0 and bitrate not in (0, 15) and rate != 3


_PCM_BITS = {"audio/l8": 8, "audio/l16": 16, "audio/l24": 24}
_PCM = set(_PCM_BITS) | {"audio/pcm", "audio/raw", "audio/x-raw"}
_EXT = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/flac": "flac",
    "audio/webm": "webm",
    "video/webm": "webm",
    "image/jpeg": "jpg",
}


def _declared(raw: bytes, declared: str) -> MediaInfo:
    parts = [p.strip() for p in str(declared).split(";")]
    base = parts[0]
    params: Dict[str, str] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip().lower()] = v.strip().strip('"')
    low = base.lower()
    if not base or low == OCTET:
        return MediaInfo()
    if low in _PCM:
        info = MediaInfo(base, "pcm")
        rate, channels = _int(params.get("rate")), _int(params.get("channels")) or 1
        bits = _PCM_BITS.get(low) or _int(params.get("bits")) or 16
        if rate:
            info.sample_rate, info.channels = rate, channels
            info.duration_s = len(raw) / (rate * channels * (bits / 8.0))
        return info
    ext = _EXT.get(low) or (mimetypes.guess_extension(low) or "").lstrip(".")
    ext = ext if ext and ext.isalnum() and len(ext) <= 8 else "bin"
    return MediaInfo(base, ext)


def _int(v: Any) -> Optional[int]:
    try:
        n = int(str(v))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


# ── stores ──────────────────────────────────────────────────────────────


class MediaStore(ABC):
    """Where trace blobs live, by SHA-256. A store writes a blob once:
    :meth:`put` of bytes it already holds does nothing but return the hash."""

    #: Recorded in each reference, so a reader knows where to look.
    name: str = "media"

    @abstractmethod
    def put(self, data: bytes, info: Optional[MediaInfo] = None) -> str:
        """Keep *data* (once); return its SHA-256 hex digest."""

    @abstractmethod
    def get(self, sha: str) -> Optional[bytes]:
        """The bytes, or ``None`` when the store has no such blob."""

    @abstractmethod
    def exists(self, sha: str) -> bool:
        """Whether the store holds *sha*."""

    @abstractmethod
    def delete(self, sha: str) -> bool:
        """Remove *sha*; return whether there was anything to remove."""

    @abstractmethod
    def keys(self) -> Iterator[Tuple[str, float]]:
        """Every blob held, as ``(sha, written_at epoch seconds)``."""


class LocalMediaStore(MediaStore):
    """Blobs in a directory: ``<root>/<sha[:2]>/<sha>.<ext>``.

    The extension is the detected type's, so a file opens in the right
    tool. Writes go through a temporary file and :func:`os.replace`, so a
    reader never sees half a blob and two writers of the same bytes both
    succeed.
    """

    name = "local"

    def __init__(self, root: Any):
        self.root = Path(root)

    def _dir(self, sha: str) -> Path:
        return self.root / sha[:2]

    def path(self, sha: str) -> Optional[Path]:
        """The file holding *sha*, or ``None``."""
        if not isinstance(sha, str) or not _HEX64.match(sha):
            return None
        d = self._dir(sha)
        if not d.is_dir():
            return None
        for p in d.iterdir():
            if p.name.split(".", 1)[0] == sha:
                return p
        return None

    def put(self, data: bytes, info: Optional[MediaInfo] = None) -> str:
        data = bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        if self.path(sha) is not None:
            return sha
        info = info or detect_media(data)
        d = self._dir(sha)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{sha}.{uuid.uuid4().hex}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, d / f"{sha}.{info.ext}")
        return sha

    def get(self, sha: str) -> Optional[bytes]:
        p = self.path(sha)
        return p.read_bytes() if p is not None else None

    def exists(self, sha: str) -> bool:
        return self.path(sha) is not None

    def delete(self, sha: str) -> bool:
        p = self.path(sha)
        if p is None:
            return False
        p.unlink(missing_ok=True)
        return True

    def keys(self) -> Iterator[Tuple[str, float]]:
        if not self.root.is_dir():
            return
        for shard in sorted(self.root.iterdir()):
            if not shard.is_dir() or len(shard.name) != 2:
                continue
            for p in sorted(shard.iterdir()):
                sha = p.name.split(".", 1)[0]
                if not p.name.startswith(".") and _HEX64.match(sha):
                    try:
                        yield sha, p.stat().st_mtime
                    except OSError:
                        continue


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


def _put(raw: bytes, store: MediaStore, declared: Optional[str]) -> Dict[str, Any]:
    info = detect_media(raw, declared)
    return info.ref(store.put(raw, info), store.name)
