"""Reading Office Open XML packages (DOCX, PPTX, XLSX) safely with the stdlib.

Office files are untrusted input. Members are read through :class:`Package`,
which caps the uncompressed size of any member and of the whole package (zip
bombs), and parsed with ``defusedxml`` (entity expansion, external entities).
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from typing import Dict, Optional
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as SafeET

from operonx_kb.errors import DocumentParseError

__all__ = ["Package", "NS", "q", "PKG_REL", "format_counter"]

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
}

PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def q(tag: str) -> str:
    """``"w:p"`` → ``"{http://…/main}p"``."""
    prefix, local = tag.split(":")
    return f"{{{NS[prefix]}}}{local}"


class Package:
    """A zip package with bounded reads.

    Args:
        data: The file bytes.
        kind: ``"DOCX"`` etc., for messages.
        max_member: Largest uncompressed member, bytes.
        max_total: Largest total uncompressed size, bytes.

    Raises:
        DocumentParseError: Not a zip, or over the limits.
    """

    def __init__(
        self, data: bytes, kind: str, max_member: int = 64 << 20, max_total: int = 512 << 20
    ):
        self.kind = kind
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise DocumentParseError(f"not a {kind} file: the bytes are not a zip package") from exc
        total = 0
        for info in self.zip.infolist():
            if info.file_size > max_member:
                raise DocumentParseError(
                    f"{kind} member {info.filename!r} is {info.file_size} bytes uncompressed, over the "
                    f"{max_member}-byte limit; refusing it as a possible zip bomb"
                )
            total += info.file_size
        if total > max_total:
            raise DocumentParseError(
                f"{kind} is {total} bytes uncompressed, over the {max_total}-byte limit"
            )
        self.names = set(self.zip.namelist())

    def has(self, name: str) -> bool:
        return name in self.names

    def xml(self, name: str) -> Optional[Element]:
        """The parsed member, or ``None`` when absent."""
        if name not in self.names:
            return None
        try:
            return SafeET.fromstring(self.zip.read(name))
        except Exception as exc:  # defusedxml raises several types
            raise DocumentParseError(
                f"{self.kind} member {name!r} is not valid XML", {"error": str(exc)}
            ) from exc

    def rels(self, part: str) -> Dict[str, str]:
        """Relationship id → target part path for ``part`` (resolved, package-absolute)."""
        folder, base = posixpath.split(part)
        root = self.xml(posixpath.join(folder, "_rels", base + ".rels"))
        out: Dict[str, str] = {}
        if root is None:
            return out
        for rel in root.findall(f"{{{PKG_REL}}}Relationship"):
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target") or ""
            path = (
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.normpath(posixpath.join(folder, target))
            )
            out[rel.get("Id") or ""] = path
        return out


def _roman(n: int) -> str:
    out = ""
    for value, sym in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                       (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):  # fmt: skip
        while n >= value:
            out += sym
            n -= value
    return out


def format_counter(n: int, fmt: str) -> str:
    """A list counter in a Word ``numFmt``."""
    if fmt in ("lowerLetter", "upperLetter"):
        s = ""
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = chr(ord("a") + r) + s
        return s.upper() if fmt == "upperLetter" else s
    if fmt in ("lowerRoman", "upperRoman"):
        s = _roman(n)
        return s if fmt == "upperRoman" else s.lower()
    if fmt == "decimalZero":
        return f"{n:02d}"
    return str(n)
