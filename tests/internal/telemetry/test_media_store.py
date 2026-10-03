"""Media detection and the local media store.

The gates:

* every format the detector names is detected from real bytes — WAV made
  with the ``wave`` module, the rest from minimal valid headers — and its
  duration is read where the header carries one;
* raw PCM, which has no header, takes the rate a ``Media`` declares in its
  mime parameters; the bytes win whenever they identify a format;
* the store is content-addressed: the same audio is written once, and a
  ref names the hash, the type and the store;
* the offload walk stores every ``Media`` and any large ``bytes``, leaves
  small values inline, and never mutates its input.
"""

from __future__ import annotations

import io
import struct
import wave
import zlib

import pytest

from operonx.core.media import Media
from operonx.telemetry.media import (
    OCTET,
    LocalMediaStore,
    detect_media,
    offload_to_store,
)

# -- samples ---------------------------------------------------------------------


def wav_bytes(seconds: float = 1.5, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate) * channels * (width // 2 or 1))
    return buf.getvalue()


def png_bytes() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 32
GIF = b"GIF89a\x01\x00\x01\x00\x80\x00\x00" + b"\x00" * 16
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
ID3_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x64" + b"\x00" * 64
# MPEG-1 Layer III, 128 kbps, 44.1 kHz frame header, no ID3 tag
SYNC_MP3 = b"\xff\xfb\x90\x64" + b"\x00" * 413 + b"\xff\xfb\x90\x64" + b"\x00" * 64


def flac_bytes(rate: int = 44100, channels: int = 2, bits: int = 16, samples: int = 88200) -> bytes:
    # STREAMINFO: block sizes (2+2), frame sizes (3+3), then 20 bits rate,
    # 3 bits channels-1, 5 bits bits-1, 36 bits total samples, then MD5.
    packed = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | samples
    info = struct.pack(">HH", 4096, 4096) + b"\x00" * 6 + packed.to_bytes(8, "big") + b"\x00" * 16
    return b"fLaC" + bytes([0x80]) + len(info).to_bytes(3, "big") + info


def ogg_page(payload: bytes, granule: int, seq: int, flags: int = 0) -> bytes:
    header = b"OggS" + bytes([0, flags]) + struct.pack("<qIII", granule, 1, seq, 0)
    return header + bytes([1, len(payload)]) + payload


def opus_bytes(seconds: float = 2.0, pre_skip: int = 312) -> bytes:
    head = b"OpusHead" + bytes([1, 1]) + struct.pack("<HIhB", pre_skip, 48000, 0, 0)
    tags = b"OpusTags" + struct.pack("<I", 0) + struct.pack("<I", 0)
    end = int(seconds * 48000) + pre_skip
    return (
        ogg_page(head, 0, 0, flags=2)
        + ogg_page(tags, 0, 1)
        + ogg_page(b"\x00" * 40, end, 2, flags=4)
    )


def vorbis_bytes() -> bytes:
    return ogg_page(b"\x01vorbis" + b"\x00" * 22, 0, 0, flags=2)


def webm_bytes(doctype: bytes = b"webm") -> bytes:
    # EBML header element with a DocType (0x4282) child
    doc = b"\x42\x82" + bytes([0x80 | len(doctype)]) + doctype
    return b"\x1a\x45\xdf\xa3" + bytes([0x80 | len(doc)]) + doc + b"\x00" * 16


def npy_bytes() -> bytes:
    np = pytest.importorskip("numpy")
    buf = io.BytesIO()
    np.save(buf, np.zeros(4, dtype="float32"))
    return buf.getvalue()


# -- detection --------------------------------------------------------------------


def test_wav_header_gives_rate_channels_and_duration():
    info = detect_media(wav_bytes(1.5, rate=16000, channels=1))
    assert (info.mime, info.ext) == ("audio/wav", "wav")
    assert info.sample_rate == 16000 and info.channels == 1
    assert info.duration_s == pytest.approx(1.5, abs=1e-3)
    stereo = detect_media(wav_bytes(0.25, rate=8000, channels=2))
    assert stereo.channels == 2 and stereo.duration_s == pytest.approx(0.25, abs=1e-3)


@pytest.mark.parametrize(
    "data, mime, ext",
    [
        (png_bytes(), "image/png", "png"),
        (JPEG, "image/jpeg", "jpg"),
        (GIF, "image/gif", "gif"),
        (WEBP, "image/webp", "webp"),
        (PDF, "application/pdf", "pdf"),
        (ID3_MP3, "audio/mpeg", "mp3"),
        (SYNC_MP3, "audio/mpeg", "mp3"),
        (vorbis_bytes(), "audio/ogg", "ogg"),
        (webm_bytes(), "video/webm", "webm"),
        (webm_bytes(b"matroska"), "video/x-matroska", "mkv"),
        (b"just some text, nothing binary about it" * 4, OCTET, "bin"),
        (b"", OCTET, "bin"),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_magic_bytes(data, mime, ext):
    info = detect_media(data)
    assert (info.mime, info.ext) == (mime, ext)
    assert info.size == len(data)


def test_flac_streaminfo_gives_duration():
    info = detect_media(flac_bytes(rate=44100, channels=2, samples=88200))
    assert (info.mime, info.sample_rate, info.channels) == ("audio/flac", 44100, 2)
    assert info.duration_s == pytest.approx(2.0)


def test_ogg_opus_duration_from_the_last_granule():
    info = detect_media(opus_bytes(2.0))
    assert (info.mime, info.ext, info.channels) == ("audio/opus", "opus", 1)
    assert info.sample_rate == 48000 and info.duration_s == pytest.approx(2.0)


def test_npy_is_recognised():
    assert detect_media(npy_bytes()).mime == "application/x-npy"


def test_a_lone_ff_byte_pair_is_not_an_mp3():
    # 0xFFFB alone with an invalid bitrate index (1111) is not a frame
    assert detect_media(b"\xff\xfb\xf0\x00" + b"\x00" * 64).mime == OCTET


def test_raw_pcm_takes_the_declared_rate():
    pcm = b"\x00\x00" * 16000  # one second of 16-bit mono at 16 kHz
    info = detect_media(pcm, "audio/L16;rate=16000;channels=1")
    assert (info.mime, info.ext, info.sample_rate, info.channels) == ("audio/L16", "pcm", 16000, 1)
    assert info.duration_s == pytest.approx(1.0)
    eight = detect_media(pcm, "audio/pcm; rate=8000; channels=2; bits=16")
    assert eight.duration_s == pytest.approx(1.0) and eight.channels == 2
    # a declared type without a rate keeps its mime, with no duration
    assert detect_media(pcm, "audio/pcm").duration_s is None
    # unknown bytes keep a declared non-PCM type too
    assert detect_media(b"\x01\x02" * 40, "application/x-thing").mime == "application/x-thing"


def test_the_bytes_win_over_a_wrong_declaration():
    info = detect_media(wav_bytes(0.5), "audio/mp3")
    assert info.mime == "audio/wav" and info.duration_s == pytest.approx(0.5, abs=1e-3)


def test_truncated_headers_never_raise():
    whole = wav_bytes(0.1)
    for n in range(0, 60):
        detect_media(whole[:n])
    for blob in (flac_bytes()[:20], opus_bytes()[:30], webm_bytes()[:6], png_bytes()[:9]):
        detect_media(blob)


# -- the local store ------------------------------------------------------------------


def test_same_bytes_are_stored_once(tmp_path):
    store = LocalMediaStore(tmp_path / "media")
    audio = wav_bytes(0.2)
    a = store.put(audio, detect_media(audio))
    b = store.put(audio, detect_media(audio))
    assert a == b and len(a) == 64
    files = [p for p in (tmp_path / "media").rglob("*") if p.is_file()]
    assert len(files) == 1 and files[0].name == f"{a}.wav" and files[0].parent.name == a[:2]
    assert store.get(a) == audio and store.exists(a) and store.path(a) == files[0]
    # the same bytes declared differently are still one file
    c = store.put(audio, detect_media(audio, "application/octet-stream"))
    assert c == a and len([p for p in (tmp_path / "media").rglob("*") if p.is_file()]) == 1
    assert [k for k, _ in store.keys()] == [a]
    assert store.delete(a) and not store.exists(a) and store.get(a) is None
    assert not store.delete(a)


def test_store_rejects_keys_that_are_not_hashes(tmp_path):
    store = LocalMediaStore(tmp_path)
    assert store.get("../../etc/passwd") is None and store.path("x") is None


# -- the offload walk -----------------------------------------------------------------


def test_offload_stores_media_and_large_bytes_and_keeps_the_rest(tmp_path):
    store = LocalMediaStore(tmp_path)
    audio = wav_bytes(1.0)
    payload = {
        "voice": Media(audio, "audio/wav"),
        "tiny": Media(b"\x00\x01", "application/octet-stream"),  # Media is stored at any size
        "blob": b"\x00" * 4096,
        "small": b"\x00" * 10,
        "nested": [{"img": png_bytes() * 40}],
        "url": Media("https://example.com/a.png", "image/png"),
        "text": "hello",
    }
    before = dict(payload)
    out = offload_to_store(payload, store, threshold=1024)
    assert payload == before  # not mutated
    voice = out["voice"]
    assert voice["$media"] == store.put(audio, detect_media(audio))
    assert voice["mime"] == "audio/wav" and voice["size"] == len(audio)
    assert voice["duration_s"] == pytest.approx(1.0, abs=1e-3) and voice["store"] == "local"
    assert voice["sample_rate"] == 16000 and voice["channels"] == 1
    assert out["tiny"]["$media"] and out["tiny"]["size"] == 2
    assert out["blob"]["mime"] == OCTET and "duration_s" not in out["blob"]
    assert out["small"] == b"\x00" * 10
    assert out["nested"][0]["img"]["mime"] == "image/png"
    assert out["url"] == {"$media_url": "https://example.com/a.png", "mime": "image/png"}
    assert out["text"] == "hello"


def test_offload_honours_a_declared_pcm_rate(tmp_path):
    store = LocalMediaStore(tmp_path)
    out = offload_to_store(
        {"pcm": Media(b"\x00\x00" * 8000, "audio/L16;rate=8000")}, store, threshold=1024
    )
    assert out["pcm"]["mime"] == "audio/L16" and out["pcm"]["duration_s"] == pytest.approx(1.0)


def test_offload_numpy(tmp_path):
    np = pytest.importorskip("numpy")
    out = offload_to_store({"a": np.zeros(1000, dtype="float32")}, LocalMediaStore(tmp_path), 1024)
    assert out["a"]["mime"] == "application/x-npy"
