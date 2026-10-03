"""Decoding source bytes to text, explicitly.

UTF-8 (with or without BOM) and BOM-marked UTF-16/32 are recognised. Anything
else must be named: a parser built with ``encoding="cp1258"`` decodes with it.
Bytes that do not decode raise :class:`DocumentParseError` naming the setting to
change — a wrong guess would put mojibake into the canonical text silently.
"""

from __future__ import annotations

import codecs
from typing import Optional

from operonx_kb.errors import DocumentParseError

__all__ = ["decode_text"]

_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def decode_text(data: bytes, encoding: Optional[str] = None, *, what: str = "text") -> str:
    """Decode ``data``: the given ``encoding``, else by BOM, else strict UTF-8.

    Raises:
        DocumentParseError: The bytes are not valid in that encoding.
    """
    if encoding is None:
        for bom, name in _BOMS:
            if data.startswith(bom):
                encoding = name
                break
        else:
            encoding = "utf-8"
    try:
        return data.decode(encoding)
    except (UnicodeDecodeError, LookupError) as exc:
        raise DocumentParseError(
            f"cannot decode {what} as {encoding}. If the file uses a legacy encoding, build the "
            "parser with it, e.g. PlainTextParser(encoding='cp1258').",
            {"error": str(exc)},
        ) from exc
