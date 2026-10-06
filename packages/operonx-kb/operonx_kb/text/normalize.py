"""Text normalisation: the one place that decides what "the same text" means.

Every hash over text (element ``content_sha``, chunk ``content_sha``) and every
character of the canonical text goes through these functions, so two parsers
that read the same words produce the same bytes.

Rules (track5 §6.1, "NFC-normalized and whitespace-collapsed"):

- Unicode NFC. Vietnamese is the case that matters: ``"ệ"`` arrives both
  precomposed (U+1EC7) and as ``e`` + two combining marks, and must hash the same.
- ``\\r\\n`` and ``\\r`` become ``\\n``.
- Characters that render as nothing are dropped: zero-width space (U+200B),
  byte-order mark (U+FEFF), soft hyphen (U+00AD), and C0/C1 controls other than
  ``\\n`` and ``\\t``. Zero-width (non-)joiners are kept: they change how
  Indic and Persian text and emoji render.
- Every other Unicode space (NBSP, thin space, ideographic space, tab) is a plain
  space.

:func:`normalize_inline` then collapses all whitespace to single spaces (one
line of prose); :func:`normalize_block` keeps line structure and indentation
(code, preformatted text).
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["normalize_inline", "normalize_block", "clean_chars"]

# C0 and C1 control characters except \t (\x09) and \n (\x0a), plus the
# invisible characters listed in the module docstring.
_DROP = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f­​﻿]")
# Unicode space separators (category Zs) other than the ASCII space, plus tab.
_SPACES = re.compile("[\t   -   　]")
_RUNS = re.compile(r"\s+")
_TRAILING = re.compile(r"[ ]+\n")


def clean_chars(text: str) -> str:
    """NFC, newline unification, invisible characters dropped, odd spaces made plain."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _DROP.sub("", text)
    return _SPACES.sub(" ", text)


def normalize_inline(text: str) -> str:
    """One line: :func:`clean_chars`, then every run of whitespace becomes one space.

    Example:
        >>> normalize_inline("  Nghe\\u0302\\u0323  phép\\n năm ")
        'Nghệ phép năm'
    """
    return _RUNS.sub(" ", clean_chars(text)).strip()


def normalize_block(text: str) -> str:
    """Keep lines and indentation: :func:`clean_chars`, trailing spaces and
    leading/trailing blank lines removed.

    Used for code and preformatted text, where a collapsed space changes meaning.
    """
    text = _TRAILING.sub("\n", clean_chars(text))
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(line.rstrip() for line in lines)
