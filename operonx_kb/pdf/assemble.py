"""Text assembly rules for PDF blocks, ported from docling.

- :func:`join_lines` is docling's ``PageAssembleModel.sanitize_text``
  (``page_assemble_model.py``): lines are joined with a space, except that a
  line ending in a hyphen attached to a word, followed by a line starting with a
  word, is de-hyphenated ("infor-" + "mation" → "information"). A free-standing
  hyphen keeps a space after it.
- :data:`LIGATURES` expands presentation-form ligatures (``ﬁ`` → ``fi``) and
  swallows the stray space some PDFs put after them ("ﬁ eld" → "field").
- :func:`split_list_marker` is docling's ``ListItemMarkerProcessor``
  (``list_marker_processor.py``): bullet and enumeration markers.
- :func:`continues` is docling's cross-column/page merge test
  (``reading_order_rb.py``, predict_merges): a text ending in a lowercase
  letter, comma or hyphen continues into a text that starts with a letter.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

__all__ = [
    "join_lines",
    "fix_ligatures",
    "split_list_marker",
    "is_caption",
    "continues",
    "join_continued",
]

_LIGATURE_MAP = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "ﬅ": "st", "ﬆ": "st", "Ĳ": "IJ", "ĳ": "ij",
}  # fmt: skip
_LIGATURE_RE = re.compile("([ﬀ-ﬆ])( (?=\\w))?")
_WORD = re.compile(r"\b\w+\b")


def fix_ligatures(text: str) -> str:
    """Expand ``ﬀ ﬁ ﬂ ﬃ ﬄ ﬅ ﬆ Ĳ ĳ``; drop a space between a ligature and the rest of its word."""
    text = _LIGATURE_RE.sub(lambda m: _LIGATURE_MAP[m.group(1)], text)
    return text.replace("Ĳ", "IJ").replace("ĳ", "ij").replace("", "")


def join_lines(lines: List[str]) -> str:
    """Join the lines of one block into its text (docling's sanitize_text)."""
    lines = [line.replace("\x02", "-").strip() for line in lines if line.strip()]
    if not lines:
        return ""
    out = list(lines)
    for ix in range(1, len(out)):
        prev = out[ix - 1]
        line = out[ix]
        if prev.endswith("-"):
            prev_words = _WORD.findall(prev)
            line_words = _WORD.findall(line)
            attached = len(prev) > 1 and prev[-2].isalnum()
            if (
                attached
                and prev_words
                and line_words
                and prev_words[-1].isalnum()
                and line_words[0].isalnum()
            ):
                out[ix - 1] = prev[:-1]
            elif not attached:
                out[ix - 1] = prev + " "
        else:
            out[ix - 1] = prev + " "
    return fix_ligatures("".join(out)).strip()


_BULLET = r"[-*+•·‣⁃◦▪▫■□●○►▶▸➤➢✓✔✗✘–—]"
_ENUM = (
    r"\d+(?:\.\d+)+\.?"  # 1.2 / 1.2.3.
    r"|\d+[.)]"  # 1. 1)
    r"|\(\d+\)|\[\d+\]"  # (1) [1]
    r"|\(?[ivxlcdm]+[.)]"  # i. ii) (iv)
    r"|\(?[IVXLCDM]+[.)]"
    r"|\(?[a-z][.)]|\(?[A-Z][.)]"  # a. b) (c) A.
)
_MARKER = re.compile(rf"^\s*({_BULLET}|{_ENUM})\s+(\S.*)$", re.S)


def split_list_marker(text: str) -> Optional[Tuple[str, bool, str]]:
    """``(marker, ordered, rest)`` when ``text`` starts with a list marker, else ``None``.

    A dotted number followed by a capitalised short phrase is ambiguous with a
    numbered heading; the caller decides with font evidence first.
    """
    m = _MARKER.match(text)
    if not m:
        return None
    marker = m.group(1)
    ordered = not re.fullmatch(_BULLET, marker)
    return marker, ordered, m.group(2)


_CAPTION = re.compile(
    r"^(fig(ure)?|tab(le)?|chart|exhibit|listing|bảng|hình|biểu đồ|sơ đồ)\.?\s*[\dIVX]+[a-z]?\s*[.:\-–—]",
    re.I,
)


def is_caption(text: str) -> bool:
    """Starts like "Figure 2:", "Table 1.", "Bảng 3 –"."""
    return bool(_CAPTION.match(text.strip()))


_ENDS_OPEN = re.compile(r".+([a-z,\-­])(\s*)$", re.S)
_STARTS_WORD = re.compile(r"^(\s*[a-zA-ZÀ-ɏ])(.+)", re.S)


def continues(first: str, second: str) -> bool:
    """Whether ``second`` continues the sentence ``first`` broke off (docling's merge rule)."""
    return bool(_ENDS_OPEN.match(first)) and bool(_STARTS_WORD.match(second))


def join_continued(first: str, second: str) -> str:
    """Join a text continued across a column or page (docling's merge join)."""
    first = first.rstrip()
    second = second.lstrip()
    if first.endswith("­") or (first.endswith("-") and second[:1].islower()):
        return first[:-1] + second
    return f"{first} {second}"
