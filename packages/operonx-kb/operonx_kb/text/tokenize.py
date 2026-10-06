"""Token counting for chunk budgets.

The default :class:`RegexTokenizer` counts words and punctuation marks, with a
long word counted as several pieces the way subword tokenizers split it. It is
deterministic and needs no download, so CI runs offline (PLAN D7). A model's own
tokenizer plugs in through the :class:`Tokenizer` protocol; its fingerprint goes
into the chunker's, so switching tokenizers is a visible re-chunk.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from operonx_kb.model.ids import fingerprint

__all__ = ["Tokenizer", "RegexTokenizer"]


@runtime_checkable
class Tokenizer(Protocol):
    def count(self, text: str) -> int: ...

    def fingerprint(self) -> str: ...


_PIECES = re.compile(r"\w+|[^\w\s]", re.UNICODE)


class RegexTokenizer:
    """Words and punctuation; a word of more than ``piece`` characters counts
    ``ceil(len / piece)`` tokens.

    With ``piece=6`` English prose counts within about 15% of cl100k, which is
    all a chunk budget needs.
    """

    def __init__(self, piece: int = 6):
        if piece < 1:
            raise ValueError("RegexTokenizer(piece=...) must be at least 1")
        self.piece = piece

    def count(self, text: str) -> int:
        total = 0
        for match in _PIECES.finditer(text):
            n = match.end() - match.start()
            total += -(-n // self.piece) if n > self.piece else 1
        return total

    def fingerprint(self) -> str:
        return fingerprint("operonx_kb.text.RegexTokenizer", "1", {"piece": self.piece})
