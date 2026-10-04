"""The lexical analyzer: what a word is, for the lexical index (PLAN R2, track5 §9.3).

Chunk text and query text go through the same :class:`Analyzer`, and the
lexical backends store and match its tokens as given (FTS5's ``ascii``
tokenizer with ``_`` as a token character, a Postgres ``tsvector`` built from
the tokens), so SQLite and Postgres rank the same words.

- ``simple``: :func:`~operonx_kb.text.normalize.clean_chars` (NFC first, so a
  decomposed ``ệ`` and a precomposed one are the same token), casefold, then
  runs of letters and digits. An underscore is a separator, because ``_``
  joins the bigrams below.
- ``vi``: ``simple`` plus each pair of adjacent words of a phrase, joined by
  ``_`` (``nghỉ phép`` → ``nghỉ_phép``). Vietnamese writes a word's syllables
  apart, and most words are two syllables, so the bigram stands in for word
  segmentation without a segmenter dependency. A phrase ends at anything but
  whitespace (punctuation, a line of a table).
- ``fold_diacritics``: strip combining marks after NFD and map ``đ`` to ``d``,
  so ``nghi phep`` finds ``nghỉ phép``. It also merges words that differ only
  by their marks (``bán``, ``bàn``, ``ban``); whether it pays is measured, not
  assumed (``docs/bench/k2.md``).
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Tuple

from operonx_kb.model.collection import AnalyzerSpec
from operonx_kb.model.ids import fingerprint
from operonx_kb.text.normalize import clean_chars

__all__ = ["Analyzer", "fold_diacritics"]

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_SPACE_ONLY = re.compile(r"\s+")
_BARRED_D = str.maketrans({"đ": "d", "Đ": "D"})


def fold_diacritics(text: str) -> str:
    """``text`` without combining marks, ``đ``/``Đ`` as ``d``/``D``, NFC again."""
    decomposed = unicodedata.normalize("NFD", text.translate(_BARRED_D))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return unicodedata.normalize("NFC", stripped)


class Analyzer:
    """Tokenises text for the lexical index.

    Args:
        spec: Which analyzer and whether it folds diacritics.
    """

    VERSION = "1"

    def __init__(self, spec: AnalyzerSpec):
        self.spec = spec

    def _words(self, text: str) -> Tuple[List[re.Match], str]:
        text = clean_chars(text).casefold()
        if self.spec.fold_diacritics:
            text = fold_diacritics(text)
        return list(_WORD.finditer(text)), text

    def tokens(self, text: str) -> List[str]:
        """The tokens of ``text``, in order, with repeats (what a document holds)."""
        words, folded = self._words(text)
        out = [m.group() for m in words]
        if self.spec.kind == "vi":
            for a, b in zip(words, words[1:]):
                if _SPACE_ONLY.fullmatch(folded[a.end() : b.start()]):
                    out.append(f"{a.group()}_{b.group()}")
        return out

    def query_tokens(self, text: str) -> List[str]:
        """The distinct tokens of a query, in first-seen order."""
        return list(dict.fromkeys(self.tokens(text)))

    def fingerprint(self) -> str:
        return fingerprint("operonx_kb.text.Analyzer", self.VERSION, self.spec.model_dump())
